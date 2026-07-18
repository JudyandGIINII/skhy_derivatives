"""공급자 간 시세 대조와 오래된 참조가 강제 (FR-05, PRD 7.2, 14.2)."""

from __future__ import annotations

from decimal import Decimal

from skhy_research.domain.market import MarketQuote


def check_source_divergence(
    primary: MarketQuote,
    secondary: MarketQuote,
    tolerance_pct: Decimal,
    max_time_skew_ns: int,
) -> bool:
    """동기화 허용범위 안에서 두 공급자 mid price 차이가 허용치를 넘으면 True."""
    if primary.instrument_id != secondary.instrument_id:
        raise ValueError("서로 다른 instrument_id는 대조할 수 없다")
    time_skew = abs(primary.event_time_utc - secondary.event_time_utc)
    if time_skew > max_time_skew_ns:
        # 동기화 허용범위 밖이면 비교 자체가 무의미하다 — stale_reference 판단으로 넘긴다.
        return False
    primary_mid = (primary.bid_price + primary.ask_price) / 2
    secondary_mid = (secondary.bid_price + secondary.ask_price) / 2
    if primary_mid == 0:
        return False
    diff_pct = abs(primary_mid - secondary_mid) / primary_mid * Decimal("100")
    return diff_pct > tolerance_pct


def check_stale_reference(quote: MarketQuote, as_of_utc: int, max_age_ns: int) -> bool:
    """quote가 as_of_utc 기준 max_age_ns보다 오래되었으면 True(stale_reference 강제, PRD 5.1)."""
    return (as_of_utc - quote.event_time_utc) > max_age_ns
