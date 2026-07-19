"""KRX 최신 일별 종가를 KIS 주값·Toss 대조값과 read-only 비교한다."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from skhy_research.adapters.providers.snapshot_support import parse_provider_iso_timestamp
from skhy_research.application.krx_backfill_runner import InstrumentBackfillSummary


class _KisCurrentPriceClient(Protocol):
    @property
    def environment(self) -> Literal["vps", "prod"]: ...

    def fetch_domestic_quote(self, symbol: str = "000660", market: str = "J") -> dict[str, Any]: ...


class _TossCurrentPriceClient(Protocol):
    def fetch_prices(self, symbols: list[str]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class LivePriceCrosscheck:
    instrument_id: str
    symbol: str
    krx_basis_date: date
    krx_official_close: Decimal
    kis_current_price: Decimal
    kis_received_at_utc: int
    toss_current_price: Decimal
    toss_observed_at_utc: int
    toss_received_at_utc: int
    kis_vs_krx_move_pct: Decimal
    toss_vs_krx_move_pct: Decimal
    kis_vs_toss_divergence_pct: Decimal
    max_live_move_pct: Decimal
    max_cross_source_divergence_pct: Decimal
    status: str
    anomaly_reasons: tuple[str, ...]


def crosscheck_latest_prices(
    summaries: tuple[InstrumentBackfillSummary, ...],
    *,
    kis_client: _KisCurrentPriceClient,
    toss_client: _TossCurrentPriceClient,
    max_live_move_pct: Decimal = Decimal("30"),
    max_cross_source_divergence_pct: Decimal = Decimal("1"),
    clock_ns: Callable[[], int] = time.time_ns,
) -> tuple[LivePriceCrosscheck, ...]:
    """서로 다른 시점의 값임을 유지한 채 가격 bound와 공급자 괴리를 탐지한다."""

    if kis_client.environment != "prod":
        raise ValueError("KIS live 교차검증은 KIS_ENV=prod만 허용한다")
    if max_live_move_pct < 0 or max_cross_source_divergence_pct < 0:
        raise ValueError("가격 bound와 공급자 괴리 tolerance는 음수일 수 없다")
    if not summaries:
        raise ValueError("교차검증할 KRX 최신 Bar가 없다")

    symbols = [summary.symbol for summary in summaries]
    toss_rows = toss_client.fetch_prices(symbols)
    toss_received_at_utc = clock_ns()
    toss_by_symbol: dict[str, dict[str, Any]] = {}
    for row in toss_rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or symbol not in symbols:
            raise ValueError("요청하지 않은 Toss symbol이 교차검증 응답에 있다")
        if symbol in toss_by_symbol:
            raise ValueError(f"Toss 교차검증 symbol 중복: {symbol}")
        toss_by_symbol[symbol] = row
    missing = set(symbols) - set(toss_by_symbol)
    if missing:
        raise ValueError(f"Toss 교차검증 현재가 누락: {sorted(missing)}")

    results: list[LivePriceCrosscheck] = []
    for summary in summaries:
        kis_row = kis_client.fetch_domestic_quote(summary.symbol)
        kis_received_at_utc = clock_ns()
        kis_price = _positive_decimal(kis_row.get("stck_prpr"), "KIS stck_prpr")
        toss_row = toss_by_symbol[summary.symbol]
        toss_price = _positive_decimal(toss_row.get("lastPrice"), "Toss lastPrice")
        toss_observed_at_utc = parse_provider_iso_timestamp(toss_row.get("timestamp"))
        latest = summary.latest_bar
        krx_basis_date = date.fromisoformat(latest.construction.source_segment.rsplit(":", 1)[-1])

        kis_vs_krx = _pct_difference(latest.close, kis_price)
        toss_vs_krx = _pct_difference(latest.close, toss_price)
        kis_vs_toss = _pct_difference(kis_price, toss_price)
        reasons: list[str] = []
        if kis_vs_krx > max_live_move_pct:
            reasons.append("KIS_CURRENT_VS_KRX_CLOSE_BOUND")
        if toss_vs_krx > max_live_move_pct:
            reasons.append("TOSS_CURRENT_VS_KRX_CLOSE_BOUND")
        if kis_vs_toss > max_cross_source_divergence_pct:
            reasons.append("KIS_VS_TOSS_DIVERGENCE")
        results.append(
            LivePriceCrosscheck(
                instrument_id=summary.instrument_id,
                symbol=summary.symbol,
                krx_basis_date=krx_basis_date,
                krx_official_close=latest.close,
                kis_current_price=kis_price,
                kis_received_at_utc=kis_received_at_utc,
                toss_current_price=toss_price,
                toss_observed_at_utc=toss_observed_at_utc,
                toss_received_at_utc=toss_received_at_utc,
                kis_vs_krx_move_pct=kis_vs_krx,
                toss_vs_krx_move_pct=toss_vs_krx,
                kis_vs_toss_divergence_pct=kis_vs_toss,
                max_live_move_pct=max_live_move_pct,
                max_cross_source_divergence_pct=max_cross_source_divergence_pct,
                status="ANOMALY" if reasons else "CONSISTENT",
                anomaly_reasons=tuple(reasons),
            )
        )
    return tuple(results)


def _positive_decimal(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field}를 Decimal로 파싱할 수 없다") from exc
    if parsed <= 0:
        raise ValueError(f"{field}는 0보다 커야 한다")
    return parsed


def _pct_difference(reference: Decimal, observed: Decimal) -> Decimal:
    if reference <= 0:
        raise ValueError("가격 비교 기준값은 0보다 커야 한다")
    return abs(observed - reference) / reference * Decimal("100")
