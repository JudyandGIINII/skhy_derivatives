"""ETP 백필과 daily-proxy walk-forward 실데이터 경로의 fixture 검증."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from datetime import time as wall_time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from skhy_research.adapters.persistence.gate_decision_store import PostgresGateDecisionStore
from skhy_research.adapters.persistence.normalized_record_store import (
    save_normalized_record_idempotent,
)
from skhy_research.application.config import load_settings
from skhy_research.application.gate_decision_seed import seed_confirmed_gate_decisions
from skhy_research.application.h1_daily_proxy_walk_forward import (
    DailyProxyBacktestConfig,
    run_h1_daily_proxy_walk_forward,
)
from skhy_research.application.krx_etp_backfill_runner import execute_krx_etp_backfill
from skhy_research.domain.calendar import local_datetime_to_utc_nanos
from skhy_research.domain.enums import (
    AdjustmentStatus,
    AssetClass,
    Currency,
    PromotionVerdict,
    QualityFlag,
    Session,
    Venue,
)
from skhy_research.domain.krx_etp import KrxEtpDailySnapshot
from skhy_research.domain.market import Bar, BarConstructionMethod
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "krx"


class _FixtureEtpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []

    def capabilities(self) -> ProviderCatalogEntry:
        return ProviderCatalogEntry(
            provider_name="krx",
            port_type="historical_data",
            catalog_version="krx-etp-test-v1",
            capabilities=frozenset(
                {ProviderCapability.HISTORICAL_BARS, ProviderCapability.INSTRUMENT_MASTER}
            ),
            license_terms_url="https://example.com/krx-terms",
            storage_redistribution_allowed=False,
            last_verified_at_utc=time.time_ns(),
            health_status=HealthStatus.HEALTHY,
        )

    def fetch_daily_etf_trades(self, trading_date: date) -> list[dict[str, Any]]:
        self.calls.append(("ETF", trading_date))
        return _rows_for_date("etf_daily_20260716.json", trading_date)

    def fetch_daily_etn_trades(self, trading_date: date) -> list[dict[str, Any]]:
        self.calls.append(("ETN", trading_date))
        return _rows_for_date("etn_daily_20260716.json", trading_date)


def _rows_for_date(fixture_name: str, trading_date: date) -> list[dict[str, Any]]:
    payload = json.loads((_FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8"))
    return [dict(row, BAS_DD=trading_date.strftime("%Y%m%d")) for row in payload["OutBlock_1"]]


def _weekday_dates(start: date, count: int) -> list[date]:
    result: list[date] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def test_etp_backfill_is_read_once_per_endpoint_and_idempotent(
    clean_pg: Engine, tmp_path: Path
) -> None:
    now = time.time_ns()
    seed_confirmed_gate_decisions(
        PostgresGateDecisionStore(clean_pg), recorded_at_utc=now
    )
    trading_dates = (date(2026, 7, 15), date(2026, 7, 16))
    clock_value = now + 1_000

    def clock_ns() -> int:
        nonlocal clock_value
        clock_value += 1
        return clock_value

    first_client = _FixtureEtpClient()
    first = execute_krx_etp_backfill(
        engine=clean_pg,
        data_root=tmp_path,
        client=first_client,
        trading_dates=trading_dates,
        min_request_interval_seconds=0,
        gate_as_of_utc=now + 1,
        clock_ns=clock_ns,
    )

    assert first_client.calls == [
        ("ETF", trading_dates[0]),
        ("ETN", trading_dates[0]),
        ("ETF", trading_dates[1]),
        ("ETN", trading_dates[1]),
    ]
    assert first.raw_inserted_count == 4
    assert first.raw_duplicate_count == 0
    assert first.product_observation_count > 0
    assert first.normalized_inserted_count == first.product_observation_count
    assert set(first.product_symbols) == {"0193T0", "0197X0", "520101"}

    second = execute_krx_etp_backfill(
        engine=clean_pg,
        data_root=tmp_path,
        client=_FixtureEtpClient(),
        trading_dates=trading_dates,
        min_request_interval_seconds=0,
        gate_as_of_utc=now + 1,
        clock_ns=clock_ns,
    )

    assert second.raw_inserted_count == 0
    assert second.raw_duplicate_count == 4
    assert second.normalized_inserted_count == 0
    assert second.normalized_duplicate_count == first.normalized_inserted_count


def test_daily_proxy_walk_forward_is_deterministic_and_stays_in_hold(
    clean_pg: Engine,
) -> None:
    trading_dates = _weekday_dates(date(2026, 1, 2), 120)
    received_base = 2_000_000_000_000_000_000
    for index, trading_date in enumerate(trading_dates):
        close = Decimal("100000") + Decimal(index % 10) * Decimal("1000")
        open_price = close - (Decimal("500") if index % 2 == 0 else Decimal("-500"))
        bar_close = local_datetime_to_utc_nanos(
            trading_date,
            wall_time(15, 30),
            Venue.KRX,
        )
        bar = Bar(
            source="KRX_OPEN_API",
            venue=Venue.KRX,
            symbol="000660",
            event_time_utc=bar_close,
            received_time_utc=received_base + index,
            currency=Currency.KRW,
            session=Session.REGULAR,
            is_delayed=True,
            adjustment_status=AdjustmentStatus.RAW,
            quality_flag=[QualityFlag.DELAYED],
            instrument_id="KRX_000660_COMMON_STOCK",
            period="1d",
            open=open_price,
            high=max(open_price, close) + Decimal("100"),
            low=min(open_price, close) - Decimal("100"),
            close=close,
            volume=Decimal("1000000"),
            turnover=Decimal("1000000000"),
            is_adjusted=False,
            construction=BarConstructionMethod(
                method="SYNTHETIC_TEST_DATA",
                source_segment=f"fixture:{trading_date.isoformat()}",
            ),
            bar_close_time_utc=bar_close,
        )
        save_normalized_record_idempotent(
            clean_pg,
            bar,
            created_at_utc=received_base + index,
            normalized_record_id=f"krx:stk_bydd_trd:000660:{trading_date:%Y%m%d}",
        )

    for index, trading_date in enumerate(trading_dates[75:-1], start=75):
        snapshot = KrxEtpDailySnapshot(
            fund_id="KRX_TEST_ETF_LEVERAGED_ETF",
            source_symbol="TESTETF",
            display_name="TEST SK하이닉스단일종목레버리지",
            asset_class=AssetClass.LEVERAGED_ETF,
            underlying_name="SK하이닉스",
            leverage_factor=Decimal("2"),
            basis_date=trading_date,
            nav_or_indicative_value=Decimal("10000"),
            listed_shares=Decimal("1000000"),
            raw_record_id=f"raw-etp-{trading_date:%Y%m%d}",
        )
        save_normalized_record_idempotent(
            clean_pg,
            snapshot,
            created_at_utc=received_base + 1_000 + index,
            normalized_record_id=f"krx:etp_daily:TESTETF:{trading_date:%Y%m%d}",
        )

    config = DailyProxyBacktestConfig(
        seed=11,
        neutral_band=Decimal("0"),
        bootstrap_resamples=100,
        permutation_count=100,
    )
    settings = load_settings("local")
    first = run_h1_daily_proxy_walk_forward(clean_pg, settings, config)
    second = run_h1_daily_proxy_walk_forward(clean_pg, settings, config)

    assert first.result_hash == second.result_hash
    assert first.to_dict() == second.to_dict()
    assert first.bar_count == 120
    assert first.etp_snapshot_count == 44
    assert first.available_feature_count == 44
    assert len(first.folds) == 2
    assert sum(fold.base.trade_count for fold in first.folds) == 44
    assert all(fold.engine_fill_count == fold.base.trade_count * 2 for fold in first.folds)
    assert first.aggregate_stress_2x.cumulative_pnl < first.aggregate_base.cumulative_pnl
    assert first.promotion.verdict is PromotionVerdict.HOLD
    assert first.promotion.promotion_eligible is False
    assert first.promotion.promotion_scope == "h1-daily-proxy-research-only"
