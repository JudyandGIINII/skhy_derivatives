"""P1-01 검증: 백필→커버리지 검증→Parquet snapshot 파이프라인.

**중요**: 여기서 사용하는 120거래일 bar 데이터는 합성(synthetic) 테스트
데이터다. 실제 KRX 시세가 아니며, `source="synthetic_test_fixture"`로
명시한다. 이 테스트는 파이프라인의 정합성(커버리지 계산, snapshot 버전관리,
공급자 대조)을 증명할 뿐, PRD의 "최소 120 KRX 거래일" 완료조건을 실데이터로
충족시키지는 않는다. G-06의 사람용 결정 문서는 `CONFIRMED`지만 G-04는
`IN_REVIEW`이고, 런타임에는 검토 완료된 두 결정을 PostgreSQL에 별도로 저장·로드해야
하므로 실제 백필은 계속 차단된다.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from skhy_research.adapters.calendars.static_holiday_provider import StaticHolidayProvider
from skhy_research.adapters.persistence.gate_decision_store import (
    PostgresGateDecisionStore,
)
from skhy_research.adapters.providers.fixture_historical_data import FixtureHistoricalDataProvider
from skhy_research.adapters.providers.fixture_support import FixtureCallGateway, FixtureScenario
from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.application.gate_registry import GateRegistry
from skhy_research.application.gate_registry_loader import load_gate_registry
from skhy_research.application.krx_backfill import BackfillGateBlockedError, backfill_daily_bars
from skhy_research.application.parquet_snapshot import ParquetSnapshotWriter
from skhy_research.application.provider_registry import ProviderRegistry
from skhy_research.application.trading_day_coverage import expected_trading_days
from skhy_research.domain.calendar import local_datetime_to_utc_nanos
from skhy_research.domain.enums import AdjustmentStatus, Currency, Session, Venue
from skhy_research.domain.gate import GateDecision, GateStatus
from skhy_research.domain.market import Bar, BarConstructionMethod
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)

_INSTRUMENT_ID = "SKHY_000660_KRX_COMMON"
_MIN_TRADING_DAYS = 120
_GATE_NOW = 1_800_000_000_000_000_000


def _confirmed_backfill_decision(gate_id: str, checksum_digit: int) -> GateDecision:
    return GateDecision(
        gate_id=gate_id,
        status=GateStatus.CONFIRMED,
        evidence_url=f"https://example.com/evidence/{gate_id}",
        evidence_checksum=f"{checksum_digit:x}" * 64,
        responsible_provider="official-provider",
        conclusion=f"{gate_id} 백필 범위 확인",
        confirmed_at_utc=_GATE_NOW,
        valid_until_utc=_GATE_NOW + 90_000_000_000_000,
        recorded_at_utc=_GATE_NOW,
    )


def _confirmed_backfill_gates() -> GateRegistry:
    registry = GateRegistry()
    for index, gate_id in enumerate(("G-04", "G-06"), start=1):
        registry.record_decision(_confirmed_backfill_decision(gate_id, index))
    return registry


def _catalog(name: str) -> ProviderCatalogEntry:
    return ProviderCatalogEntry(
        provider_name=name,
        port_type="historical_data",
        catalog_version=f"{name}-historical-data-test-v1",
        capabilities=frozenset({ProviderCapability.HISTORICAL_BARS}),
        license_terms_url="https://example.com/tos",
        storage_redistribution_allowed=False,
        last_verified_at_utc=0,
        health_status=HealthStatus.HEALTHY,
    )


def _synthetic_bar(trading_date: date, close: Decimal, source: str) -> Bar:
    close_time_utc = local_datetime_to_utc_nanos(trading_date, time(15, 30), Venue.KRX)
    event_time_utc = local_datetime_to_utc_nanos(trading_date, time(9, 0), Venue.KRX)
    return Bar(
        source=source,
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=event_time_utc,
        received_time_utc=close_time_utc,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=True,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id=_INSTRUMENT_ID,
        period="1d",
        open=close - 1000,
        high=close + 1500,
        low=close - 1500,
        close=close,
        volume=Decimal("1000000"),
        is_adjusted=False,
        construction=BarConstructionMethod(
            method="SYNTHETIC_TEST_DATA", source_segment=f"{source}:synthetic_p1_01_test"
        ),
        bar_close_time_utc=close_time_utc,
    )


def _build_registry_with_synthetic_bars(
    trading_days: list[date],
) -> tuple[ProviderRegistry, list[Bar]]:
    bars = [
        _synthetic_bar(d, Decimal(200000 + (i % 20) * 100), "synthetic_test_fixture")
        for i, d in enumerate(trading_days)
    ]
    secondary_bars = [
        _synthetic_bar(d, bar.close, "synthetic_test_fixture_secondary")
        for d, bar in zip(trading_days, bars, strict=True)
    ]

    registry = ProviderRegistry()
    registry.register_historical_data(
        "krx",
        FixtureHistoricalDataProvider(
            catalog_entry=_catalog("krx"),
            gateway=FixtureCallGateway("krx", require_auth=False),
            bars_scenario=FixtureScenario(payload=[b.model_dump(mode="json") for b in bars]),
        ),
    )
    registry.register_historical_data(
        "kis",
        FixtureHistoricalDataProvider(
            catalog_entry=_catalog("kis"),
            gateway=FixtureCallGateway("kis", require_auth=False),
            bars_scenario=FixtureScenario(payload=[b.model_dump(mode="json") for b in secondary_bars]),
        ),
    )
    return registry, bars


def test_backfill_pipeline_meets_120_trading_day_minimum_with_synthetic_data(tmp_path: Path) -> None:
    calendar_resolver = CalendarResolver(StaticHolidayProvider())
    start = date(2026, 1, 2)
    end = date(2026, 7, 17)
    all_trading_days = expected_trading_days(calendar_resolver, Venue.KRX, start, end)
    assert len(all_trading_days) >= _MIN_TRADING_DAYS  # 테스트 구간 자체가 충분히 긴지 확인
    trading_days = all_trading_days[:_MIN_TRADING_DAYS]
    actual_end = trading_days[-1]

    registry, _ = _build_registry_with_synthetic_bars(trading_days)
    start_utc = local_datetime_to_utc_nanos(start, time(0, 0), Venue.KRX)
    end_utc = local_datetime_to_utc_nanos(actual_end, time(23, 59), Venue.KRX)

    result = backfill_daily_bars(
        registry,
        calendar_resolver,
        Venue.KRX,
        primary_provider_name="krx",
        instrument_id=_INSTRUMENT_ID,
        start=start,
        end=actual_end,
        start_utc=start_utc,
        end_utc=end_utc,
        gate_registry=_confirmed_backfill_gates(),
        gate_as_of_utc=_GATE_NOW,
        secondary_provider_name="kis",
    )

    assert result.coverage.covered_trading_days == _MIN_TRADING_DAYS
    assert result.coverage.meets_minimum(_MIN_TRADING_DAYS) is True
    assert result.reconciliation_mismatches == ()  # 두 공급자 종가가 동일하게 구성됨


@pytest.mark.integration
def test_postgres_gate_decisions_load_and_unblock_synthetic_backfill(clean_pg) -> None:
    """DB의 기계용 결정이 runtime registry를 채워 실제 gate 검사 경로를 통과한다."""
    store = PostgresGateDecisionStore(clean_pg)
    for index, gate_id in enumerate(("G-04", "G-06"), start=1):
        store.save_decision(_confirmed_backfill_decision(gate_id, index))
    loaded_gate_registry = load_gate_registry(store)

    calendar_resolver = CalendarResolver(StaticHolidayProvider())
    start = date(2026, 1, 2)
    trading_days = expected_trading_days(calendar_resolver, Venue.KRX, start, start)
    provider_registry, _ = _build_registry_with_synthetic_bars(trading_days)

    result = backfill_daily_bars(
        provider_registry,
        calendar_resolver,
        Venue.KRX,
        "krx",
        _INSTRUMENT_ID,
        start,
        start,
        local_datetime_to_utc_nanos(start, time(0, 0), Venue.KRX),
        local_datetime_to_utc_nanos(start, time(23, 59), Venue.KRX),
        gate_registry=loaded_gate_registry,
        gate_as_of_utc=_GATE_NOW,
    )

    assert loaded_gate_registry.blocks("G-04", _GATE_NOW) is False
    assert loaded_gate_registry.blocks("G-06", _GATE_NOW) is False
    assert result.coverage.covered_trading_days == 1


def test_backfill_detects_missing_trading_days() -> None:
    calendar_resolver = CalendarResolver(StaticHolidayProvider())
    start = date(2026, 1, 2)
    end = date(2026, 1, 30)
    all_trading_days = expected_trading_days(calendar_resolver, Venue.KRX, start, end)
    incomplete_days = all_trading_days[:-2]  # 마지막 이틀을 일부러 뺀다

    registry, _ = _build_registry_with_synthetic_bars(incomplete_days)
    start_utc = local_datetime_to_utc_nanos(start, time(0, 0), Venue.KRX)
    end_utc = local_datetime_to_utc_nanos(end, time(23, 59), Venue.KRX)

    result = backfill_daily_bars(
        registry,
        calendar_resolver,
        Venue.KRX,
        "krx",
        _INSTRUMENT_ID,
        start,
        end,
        start_utc,
        end_utc,
        gate_registry=_confirmed_backfill_gates(),
        gate_as_of_utc=_GATE_NOW,
    )

    assert result.coverage.is_complete is False
    assert len(result.coverage.missing_dates) == 2


def test_backfill_detects_source_divergence_beyond_tolerance() -> None:
    calendar_resolver = CalendarResolver(StaticHolidayProvider())
    start = date(2026, 1, 2)
    end = date(2026, 1, 9)
    trading_days = expected_trading_days(calendar_resolver, Venue.KRX, start, end)

    registry, primary_bars = _build_registry_with_synthetic_bars(trading_days)
    # 두 번째 공급자(kis) 첫 bar를 의도적으로 크게 어긋나게 재등록한다.
    # (model_copy는 검증을 다시 돌리지 않으므로, high/low도 함께 일관되도록 새로 만든다)
    diverged_bar = _synthetic_bar(
        trading_days[0], primary_bars[0].close * 2, "synthetic_test_fixture_secondary"
    )
    registry = ProviderRegistry()
    registry.register_historical_data(
        "krx",
        FixtureHistoricalDataProvider(
            catalog_entry=_catalog("krx"),
            gateway=FixtureCallGateway("krx", require_auth=False),
            bars_scenario=FixtureScenario(payload=[b.model_dump(mode="json") for b in primary_bars]),
        ),
    )
    registry.register_historical_data(
        "kis",
        FixtureHistoricalDataProvider(
            catalog_entry=_catalog("kis"),
            gateway=FixtureCallGateway("kis", require_auth=False),
            bars_scenario=FixtureScenario(
                payload=[diverged_bar.model_dump(mode="json")]
                + [b.model_dump(mode="json") for b in primary_bars[1:]]
            ),
        ),
    )
    start_utc = local_datetime_to_utc_nanos(start, time(0, 0), Venue.KRX)
    end_utc = local_datetime_to_utc_nanos(end, time(23, 59), Venue.KRX)

    result = backfill_daily_bars(
        registry,
        calendar_resolver,
        Venue.KRX,
        "krx",
        _INSTRUMENT_ID,
        start,
        end,
        start_utc,
        end_utc,
        gate_registry=_confirmed_backfill_gates(),
        gate_as_of_utc=_GATE_NOW,
        secondary_provider_name="kis",
    )

    assert len(result.reconciliation_mismatches) == 1


def test_parquet_snapshot_round_trips_synthetic_backfill(tmp_path: Path) -> None:
    calendar_resolver = CalendarResolver(StaticHolidayProvider())
    start = date(2026, 1, 2)
    end = date(2026, 1, 30)
    trading_days = expected_trading_days(calendar_resolver, Venue.KRX, start, end)
    registry, bars = _build_registry_with_synthetic_bars(trading_days)
    start_utc = local_datetime_to_utc_nanos(start, time(0, 0), Venue.KRX)
    end_utc = local_datetime_to_utc_nanos(end, time(23, 59), Venue.KRX)

    result = backfill_daily_bars(
        registry,
        calendar_resolver,
        Venue.KRX,
        "krx",
        _INSTRUMENT_ID,
        start,
        end,
        start_utc,
        end_utc,
        gate_registry=_confirmed_backfill_gates(),
        gate_as_of_utc=_GATE_NOW,
    )

    writer = ParquetSnapshotWriter(tmp_path)
    manifest = writer.write("krx_daily_ohlcv", list(result.bars))

    assert manifest.total_record_count == len(bars)
    table = writer.read_manifest(manifest)
    assert table.num_rows == len(bars)


def test_real_backfill_is_still_gated_pending_g04_and_g06() -> None:
    """백필 진입점 자체가 미확인 gate를 검사해 provider 호출 전에 차단한다."""
    start = date(2026, 1, 2)
    end = date(2026, 1, 3)
    with pytest.raises(BackfillGateBlockedError, match="G-04.*G-06"):
        backfill_daily_bars(
            ProviderRegistry(),
            CalendarResolver(StaticHolidayProvider()),
            Venue.KRX,
            "krx",
            _INSTRUMENT_ID,
            start,
            end,
            local_datetime_to_utc_nanos(start, time(0, 0), Venue.KRX),
            local_datetime_to_utc_nanos(end, time(23, 59), Venue.KRX),
            gate_registry=GateRegistry(),
            gate_as_of_utc=_GATE_NOW,
        )
