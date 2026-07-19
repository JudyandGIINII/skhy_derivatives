"""P1-02 검증: AUM/NAV 수집이 공개시각 누락 상품을 조용히 버리지 않는다."""

from __future__ import annotations

from skhy_research.adapters.providers.fixture_reference_data import FixtureReferenceDataProvider
from skhy_research.adapters.providers.fixture_support import FixtureCallGateway, FixtureScenario
from skhy_research.application.fund_snapshot_collection import collect_fund_snapshots
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)

_NOW = 1_800_000_000_000_000_000


def _catalog(capabilities: frozenset[ProviderCapability]) -> ProviderCatalogEntry:
    return ProviderCatalogEntry(
        provider_name="hkex_issuer",
        port_type="reference_data",
        catalog_version="hkex-issuer-reference-data-test-v1",
        capabilities=capabilities,
        license_terms_url="https://example.com/tos",
        storage_redistribution_allowed=False,
        last_verified_at_utc=_NOW,
        health_status=HealthStatus.HEALTHY,
    )


def _valid_snapshot_payload() -> dict:
    return {
        "source": "hkex_issuer",
        "venue": "HKEX",
        "symbol": "7709",
        "event_time_utc": _NOW,
        "received_time_utc": _NOW,
        "currency": "HKD",
        "session": "REFERENCE",
        "is_delayed": False,
        "adjustment_status": "NOT_APPLICABLE",
        "fund_id": "HKEX_7709",
        "leverage_beta": "2",
        "aum": "1000000",
        "nav": "10.5",
        "replication_type": "SWAP",
        "published_at": _NOW,
        "effective_at": _NOW,
    }


def test_successful_collection_includes_snapshot() -> None:
    provider = FixtureReferenceDataProvider(
        catalog_entry=_catalog(frozenset({ProviderCapability.FUND_SNAPSHOT})),
        gateway=FixtureCallGateway("hkex_issuer", require_auth=False),
        fund_snapshot_scenarios={"HKEX_7709": FixtureScenario(payload=_valid_snapshot_payload())},
    )

    result = collect_fund_snapshots(provider, ["HKEX_7709"])

    assert len(result.snapshots) == 1
    assert result.snapshots[0].fund_id == "HKEX_7709"
    assert result.exclusions == ()


def test_unsupported_capability_is_excluded_with_reason_not_dropped_silently() -> None:
    provider = FixtureReferenceDataProvider(
        catalog_entry=_catalog(frozenset()),  # FUND_SNAPSHOT 미지원
        gateway=FixtureCallGateway("hkex_issuer", require_auth=False),
    )

    result = collect_fund_snapshots(provider, ["HKEX_7709"])

    assert result.snapshots == ()
    assert len(result.exclusions) == 1
    assert result.exclusions[0].fund_id == "HKEX_7709"
    assert "capability" in result.exclusions[0].reason


def test_missing_fixture_scenario_is_excluded_with_reason() -> None:
    provider = FixtureReferenceDataProvider(
        catalog_entry=_catalog(frozenset({ProviderCapability.FUND_SNAPSHOT})),
        gateway=FixtureCallGateway("hkex_issuer", require_auth=False),
        fund_snapshot_scenarios={},  # 등록되지 않음
    )

    result = collect_fund_snapshots(provider, ["UNKNOWN_FUND"])

    assert result.snapshots == ()
    assert "UNKNOWN_FUND" in result.exclusions[0].fund_id


def test_missing_published_at_causes_exclusion_not_crash_or_silent_default() -> None:
    payload = _valid_snapshot_payload()
    del payload["published_at"]  # 공개시각 누락

    provider = FixtureReferenceDataProvider(
        catalog_entry=_catalog(frozenset({ProviderCapability.FUND_SNAPSHOT})),
        gateway=FixtureCallGateway("hkex_issuer", require_auth=False),
        fund_snapshot_scenarios={"HKEX_7709": FixtureScenario(payload=payload)},
    )

    result = collect_fund_snapshots(provider, ["HKEX_7709"])

    assert result.snapshots == ()  # 전체 배치가 죽지 않고, 이 상품만 제외됨
    assert len(result.exclusions) == 1
    assert result.exclusions[0].fund_id == "HKEX_7709"


def test_one_fund_failure_does_not_block_others_in_batch() -> None:
    provider = FixtureReferenceDataProvider(
        catalog_entry=_catalog(frozenset({ProviderCapability.FUND_SNAPSHOT})),
        gateway=FixtureCallGateway("hkex_issuer", require_auth=False),
        fund_snapshot_scenarios={"GOOD_FUND": FixtureScenario(payload=_valid_snapshot_payload())},
    )

    result = collect_fund_snapshots(provider, ["BAD_FUND", "GOOD_FUND"])

    assert len(result.snapshots) == 1
    assert len(result.exclusions) == 1
