"""KRX 과거 종가와 KIS/Toss 최신값의 의미 보존·이상치 판정."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any, Literal

from skhy_research.application.krx_backfill import BackfillResult
from skhy_research.application.krx_backfill_runner import InstrumentBackfillSummary
from skhy_research.application.live_price_crosscheck import crosscheck_latest_prices
from skhy_research.application.trading_day_coverage import CoverageReport
from skhy_research.domain.calendar import local_datetime_to_utc_nanos
from skhy_research.domain.enums import AdjustmentStatus, Currency, QualityFlag, Session, Venue
from skhy_research.domain.market import Bar, BarConstructionMethod


def _summary(close: str = "100") -> InstrumentBackfillSummary:
    basis_date = date(2026, 7, 17)
    close_time = local_datetime_to_utc_nanos(basis_date, time(15, 30), Venue.KRX)
    bar = Bar(
        source="KRX_OPEN_API",
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=close_time,
        received_time_utc=close_time + 1,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=True,
        adjustment_status=AdjustmentStatus.RAW,
        quality_flag=[QualityFlag.DELAYED],
        instrument_id="KRX_000660_COMMON_STOCK",
        period="1d",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        turnover=Decimal("100"),
        is_adjusted=False,
        construction=BarConstructionMethod(
            method="VENDOR_PROVIDED", source_segment="KRX_OPEN_API:2026-07-17"
        ),
        bar_close_time_utc=close_time,
    )
    result = BackfillResult(
        bars=(bar,),
        coverage=CoverageReport(1, 1, ()),
        reconciliation_mismatches=(),
    )
    return InstrumentBackfillSummary(
        instrument_id=bar.instrument_id,
        symbol=bar.symbol,
        bar_count=1,
        latest_bar=bar,
        result=result,
    )


class _Kis:
    environment: Literal["prod"] = "prod"

    def __init__(self, price: str) -> None:
        self.price = price

    def fetch_domestic_quote(self, symbol: str = "000660", market: str = "J") -> dict[str, Any]:
        return {"stck_prpr": self.price}


class _Toss:
    def __init__(self, price: str) -> None:
        self.price = price

    def fetch_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "symbol": symbols[0],
                "lastPrice": self.price,
                "timestamp": "2026-07-18T09:00:00+09:00",
            }
        ]


def test_crosscheck_accepts_bounded_non_equal_live_values() -> None:
    result = crosscheck_latest_prices(
        (_summary(),),
        kis_client=_Kis("105"),
        toss_client=_Toss("104"),
        clock_ns=lambda: 1_800_000_000_000_000_000,
    )[0]

    assert result.status == "CONSISTENT"
    assert result.kis_vs_krx_move_pct == Decimal("5.00")
    assert result.toss_vs_krx_move_pct == Decimal("4.00")
    assert result.kis_current_price != result.krx_official_close


def test_crosscheck_reports_bound_and_cross_source_anomalies() -> None:
    result = crosscheck_latest_prices(
        (_summary(),),
        kis_client=_Kis("150"),
        toss_client=_Toss("100"),
        clock_ns=lambda: 1_800_000_000_000_000_000,
    )[0]

    assert result.status == "ANOMALY"
    assert result.anomaly_reasons == (
        "KIS_CURRENT_VS_KRX_CLOSE_BOUND",
        "KIS_VS_TOSS_DIVERGENCE",
    )
