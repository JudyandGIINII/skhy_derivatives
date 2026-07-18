"""P0-09 검증: 공급자 대조(SOURCE_DIVERGENCE)와 stale_reference 강제 (PRD 7.2, 5.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.data.reconciliation.divergence import (
    check_source_divergence,
    check_stale_reference,
)
from skhy_research.domain.enums import AdjustmentStatus, Currency, Session, Venue
from skhy_research.domain.market import MarketQuote

_NOW = 1_800_000_000_000_000_000


def _quote(source: str, instrument_id: str, mid: str, event_time_utc: int = _NOW) -> MarketQuote:
    mid_dec = Decimal(mid)
    return MarketQuote(
        source=source,
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=event_time_utc,
        received_time_utc=event_time_utc,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id=instrument_id,
        bid_price=mid_dec - 1,
        ask_price=mid_dec + 1,
        bid_size=Decimal("10"),
        ask_size=Decimal("10"),
    )


def test_divergence_within_tolerance_is_not_flagged() -> None:
    primary = _quote("kis", "SKHY_000660_KRX_COMMON", "100000")
    secondary = _quote("toss", "SKHY_000660_KRX_COMMON", "100050")  # 0.05% 차이

    assert check_source_divergence(primary, secondary, Decimal("0.5"), max_time_skew_ns=5_000_000_000) is False


def test_divergence_beyond_tolerance_is_flagged() -> None:
    primary = _quote("kis", "SKHY_000660_KRX_COMMON", "100000")
    secondary = _quote("toss", "SKHY_000660_KRX_COMMON", "102000")  # 2% 차이

    assert check_source_divergence(primary, secondary, Decimal("0.5"), max_time_skew_ns=5_000_000_000) is True


def test_divergence_check_rejects_mismatched_instrument() -> None:
    primary = _quote("kis", "A", "100000")
    secondary = _quote("toss", "B", "100000")

    with pytest.raises(ValueError, match="instrument_id"):
        check_source_divergence(primary, secondary, Decimal("0.5"), max_time_skew_ns=5_000_000_000)


def test_divergence_check_skips_comparison_beyond_time_skew() -> None:
    primary = _quote("kis", "SKHY_000660_KRX_COMMON", "100000", event_time_utc=_NOW)
    secondary = _quote(
        "toss", "SKHY_000660_KRX_COMMON", "999999999", event_time_utc=_NOW + 100_000_000_000
    )  # 100초 뒤, 큰 차이지만 동기화 범위 밖

    assert check_source_divergence(primary, secondary, Decimal("0.5"), max_time_skew_ns=5_000_000_000) is False


def test_stale_reference_true_when_older_than_max_age() -> None:
    quote = _quote("krx", "SKHY_000660_KRX_COMMON", "100000", event_time_utc=_NOW)
    as_of = _NOW + 10_000_000_000  # 10초 뒤
    assert check_stale_reference(quote, as_of, max_age_ns=2_000_000_000) is True


def test_stale_reference_false_when_within_max_age() -> None:
    quote = _quote("krx", "SKHY_000660_KRX_COMMON", "100000", event_time_utc=_NOW)
    as_of = _NOW + 1_000_000_000  # 1초 뒤
    assert check_stale_reference(quote, as_of, max_age_ns=2_000_000_000) is False
