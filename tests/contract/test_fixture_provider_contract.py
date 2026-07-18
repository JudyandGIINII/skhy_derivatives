"""P0-07 계약 테스트 (PRD 14.1): 인증 실패·rate limit·timeout·schema drift·마스킹."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.adapters.providers.fixture_historical_data import FixtureHistoricalDataProvider
from skhy_research.adapters.providers.fixture_reference_data import FixtureReferenceDataProvider
from skhy_research.adapters.providers.fixture_registry import build_fixture_provider_registry
from skhy_research.adapters.providers.fixture_support import (
    FixtureCallGateway,
    FixtureScenario,
    compute_schema_fingerprint,
)
from skhy_research.adapters.secrets.env_secret_provider import EnvSecretProvider
from skhy_research.application.capability_probe import run_capability_probe
from skhy_research.application.provider_registry import ProviderNotRegisteredError
from skhy_research.domain.enums import AdjustmentStatus
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)
from skhy_research.observability.masking import clear_registered_secrets, mask
from skhy_research.ports.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderSchemaDriftError,
    ProviderTimeoutError,
    UnsupportedCapabilityError,
)

_NOW = 1_800_000_000_000_000_000


@pytest.fixture(autouse=True)
def _isolated_secret_registry():
    clear_registered_secrets()
    yield
    clear_registered_secrets()


def _catalog(capabilities: frozenset[ProviderCapability]) -> ProviderCatalogEntry:
    return ProviderCatalogEntry(
        provider_name="test_provider",
        port_type="historical_data",
        capabilities=capabilities,
        license_terms_url="https://example.com/tos",
        storage_redistribution_allowed=False,
        last_verified_at_utc=_NOW,
        health_status=HealthStatus.HEALTHY,
    )


def test_auth_failure_without_secret_raises() -> None:
    gateway = FixtureCallGateway(
        "krx", secret_provider=EnvSecretProvider(), api_key_env="MISSING_CANARY_KEY", require_auth=True
    )
    provider = FixtureHistoricalDataProvider(
        catalog_entry=_catalog(frozenset({ProviderCapability.HISTORICAL_BARS})),
        gateway=gateway,
        bars_scenario=FixtureScenario(payload=[]),
    )
    with pytest.raises(ProviderAuthenticationError):
        provider.get_bars("000660", "1d", 0, _NOW, AdjustmentStatus.RAW)


def test_auth_success_registers_secret_for_masking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANARY_KRX_KEY", "canary-krx-value-42")
    gateway = FixtureCallGateway(
        "krx", secret_provider=EnvSecretProvider(), api_key_env="CANARY_KRX_KEY", require_auth=True
    )
    provider = FixtureHistoricalDataProvider(
        catalog_entry=_catalog(frozenset({ProviderCapability.HISTORICAL_BARS})),
        gateway=gateway,
        bars_scenario=FixtureScenario(payload=[]),
    )

    provider.get_bars("000660", "1d", 0, _NOW, AdjustmentStatus.RAW)  # 인증 통과

    leaked_text = f"error contained key={('canary-krx-value-42')}"
    assert "canary-krx-value-42" not in mask(leaked_text)


def test_rate_limit_raises_with_retry_after() -> None:
    gateway = FixtureCallGateway("kis", require_auth=False)
    provider = FixtureHistoricalDataProvider(
        catalog_entry=_catalog(frozenset({ProviderCapability.HISTORICAL_BARS})),
        gateway=gateway,
        bars_scenario=FixtureScenario(rate_limited=True, retry_after_seconds=30.0),
    )
    with pytest.raises(ProviderRateLimitError) as exc_info:
        provider.get_bars("000660", "1d", 0, _NOW, AdjustmentStatus.RAW)
    assert exc_info.value.retry_after_seconds == 30.0


def test_timeout_raises() -> None:
    gateway = FixtureCallGateway("toss", require_auth=False)
    provider = FixtureHistoricalDataProvider(
        catalog_entry=_catalog(frozenset({ProviderCapability.HISTORICAL_BARS})),
        gateway=gateway,
        bars_scenario=FixtureScenario(timed_out=True),
    )
    with pytest.raises(ProviderTimeoutError):
        provider.get_bars("000660", "1d", 0, _NOW, AdjustmentStatus.RAW)


def test_schema_drift_is_detected_when_field_type_changes() -> None:
    golden_payload = {"instrument_id": "000660", "close": Decimal("100")}
    golden_fingerprint = compute_schema_fingerprint(golden_payload)

    drifted_payload = {"instrument_id": "000660", "close": "100"}  # Decimal -> str 타입 변경
    gateway = FixtureCallGateway("krx", require_auth=False)
    scenario = FixtureScenario(payload=drifted_payload, expected_schema_fingerprint=golden_fingerprint)

    with pytest.raises(ProviderSchemaDriftError) as exc_info:
        gateway.resolve(scenario)
    assert exc_info.value.expected_fingerprint == golden_fingerprint


def test_schema_matching_fingerprint_does_not_raise() -> None:
    payload = {"instrument_id": "000660", "close": Decimal("100")}
    fingerprint = compute_schema_fingerprint(payload)
    gateway = FixtureCallGateway("krx", require_auth=False)
    scenario = FixtureScenario(payload=payload, expected_schema_fingerprint=fingerprint)

    resolved = gateway.resolve(scenario)
    assert resolved == payload


def test_unsupported_capability_for_missing_corporate_actions() -> None:
    gateway = FixtureCallGateway("krx", require_auth=False)
    provider = FixtureReferenceDataProvider(
        catalog_entry=_catalog(frozenset({ProviderCapability.INSTRUMENT_MASTER})),
        gateway=gateway,
    )
    with pytest.raises(UnsupportedCapabilityError):
        provider.get_corporate_actions("SKHY_000660_KRX_COMMON", _NOW)


def test_fixture_registry_all_providers_probe_healthy() -> None:
    registry = build_fixture_provider_registry()
    results = run_capability_probe(registry)

    assert len(results) == 7  # krx(ref+hist), kis, toss, official_filings, naver, yahoo
    assert all(r.ok for r in results)
    provider_names = {r.provider_name for r in results}
    assert provider_names == {"krx", "kis", "toss", "official_filings", "naver", "yahoo"}


def test_naver_yahoo_are_not_registered_as_realtime_market_data() -> None:
    """PRD 7.1: Naver/Yahoo는 실시간 주문판단에 사용하지 않는다 — market_data 포트에 등록되지 않아야 한다."""
    registry = build_fixture_provider_registry()
    with pytest.raises(ProviderNotRegisteredError):
        registry.get_market_data("naver")
    with pytest.raises(ProviderNotRegisteredError):
        registry.get_market_data("yahoo")


def test_krx_and_yahoo_historical_bars_carry_correct_venue() -> None:
    registry = build_fixture_provider_registry()
    krx_bars = registry.get_historical_data("krx").get_bars("000660", "1d", 0, _NOW, AdjustmentStatus.RAW)
    yahoo_bars = registry.get_historical_data("yahoo").get_bars("SKHY", "1d", 0, _NOW, AdjustmentStatus.RAW)

    assert krx_bars[0].venue.value == "KRX"
    assert yahoo_bars[0].venue.value == "NASDAQ"
