"""P1-01 검증: 백필→커버리지 검증→Parquet snapshot 파이프라인.

**중요**: 여기서 사용하는 120거래일 bar 데이터는 합성(synthetic) 테스트
데이터다. 실제 KRX 시세가 아니며, `source="synthetic_test_fixture"`로
명시한다. 이 테스트는 파이프라인의 정합성(커버리지 계산, snapshot 버전관리,
공급자 대조)을 증명할 뿐, PRD의 "최소 120 KRX 거래일" 완료조건을 실데이터로
충족시키지는 않는다 — 그 조건은 G-04/G-06 게이트 해소와 실제 KRX 키가
필요하며 두 게이트 모두 아직 `UNKNOWN`이다(`docs/decisions/gates/`).
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path

from skhy_research.adapters.calendars.static_holiday_provider import StaticHolidayProvider
from skhy_research.adapters.providers.fixture_historical_data import FixtureHistoricalDataProvider
from skhy_research.adapters.providers.fixture_support import FixtureCallGateway, FixtureScenario
from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.application.gate_registry import GateRegistry
from skhy_research.application.krx_backfill import backfill_daily_bars
from skhy_research.application.parquet_snapshot import ParquetSnapshotWriter
from skhy_research.application.provider_registry import ProviderRegistry
from skhy_research.application.trading_day_coverage import expected_trading_days
from skhy_research.domain.calendar import local_datetime_to_utc_nanos
from skhy_research.domain.enums import AdjustmentStatus, Currency, Session, Venue
from skhy_research.domain.market import Bar, BarConstructionMethod
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)

_INSTRUMENT_ID = "SKHY_000660_KRX_COMMON"
_MIN_TRADING_DAYS = 120


def _catalog(name: str) -> ProviderCatalogEntry:
    return ProviderCatalogEntry(
        provider_name=name,
        port_type="historical_data",
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
        secondary_provider_name="kis",
    )

    assert result.coverage.covered_trading_days == _MIN_TRADING_DAYS
    assert result.coverage.meets_minimum(_MIN_TRADING_DAYS) is True
    assert result.reconciliation_mismatches == ()  # 두 공급자 종가가 동일하게 구성됨


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
        registry, calendar_resolver, Venue.KRX, "krx", _INSTRUMENT_ID, start, end, start_utc, end_utc
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
        registry, calendar_resolver, Venue.KRX, "krx", _INSTRUMENT_ID, start, end, start_utc, end_utc
    )

    writer = ParquetSnapshotWriter(tmp_path)
    manifest = writer.write("krx_daily_ohlcv", list(result.bars))

    assert manifest.total_record_count == len(bars)
    table = writer.read_manifest(manifest)
    assert table.num_rows == len(bars)


def test_real_backfill_is_still_gated_pending_g04_and_g06() -> None:
    """합성 데이터로 파이프라인은 검증됐지만, 실데이터 수집은 여전히 게이트로 막혀 있다."""
    registry = GateRegistry()
    now = 1_800_000_000_000_000_000
    assert registry.blocks("G-04", now) is True
    assert registry.blocks("G-06", now) is True
