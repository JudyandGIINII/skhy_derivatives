"""P0-09 검증: 중복·역순·gap·crossed quote 탐지 (PRD 14.2)."""

from __future__ import annotations

from decimal import Decimal

from skhy_research.data.quality.detectors import SequenceState, detect_crossed_quote
from skhy_research.domain.enums import AdjustmentStatus, Currency, QualityFlag, Session, Venue
from skhy_research.domain.market import MarketQuote

_NOW = 1_800_000_000_000_000_000


def _quote(bid: str, ask: str, event_time_utc: int = _NOW) -> MarketQuote:
    return MarketQuote(
        source="kis",
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=event_time_utc,
        received_time_utc=event_time_utc,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id="SKHY_000660_KRX_COMMON",
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=Decimal("10"),
        ask_size=Decimal("10"),
    )


def test_first_event_has_no_flags() -> None:
    state = SequenceState()
    flags = state.evaluate(("kis", "SKHY_000660_KRX_COMMON"), _NOW, "dedupe-1")
    assert flags == []


def test_duplicate_dedupe_key_is_flagged() -> None:
    state = SequenceState()
    state.evaluate(("kis", "SKHY_000660_KRX_COMMON"), _NOW, "dedupe-1")
    flags = state.evaluate(("kis", "SKHY_000660_KRX_COMMON"), _NOW, "dedupe-1")
    assert flags == [QualityFlag.DUPLICATE]


def test_out_of_order_event_is_flagged() -> None:
    state = SequenceState()
    key = ("kis", "SKHY_000660_KRX_COMMON")
    state.evaluate(key, _NOW, "dedupe-1")
    flags = state.evaluate(key, _NOW - 1_000_000_000, "dedupe-2")
    assert flags == [QualityFlag.OUT_OF_ORDER]


def test_gap_exceeding_threshold_is_flagged() -> None:
    state = SequenceState()
    key = ("kis", "SKHY_000660_KRX_COMMON")
    max_gap_ns = 5_000_000_000  # 5초
    state.evaluate(key, _NOW, "dedupe-1", max_gap_ns=max_gap_ns)
    flags = state.evaluate(key, _NOW + 10_000_000_000, "dedupe-2", max_gap_ns=max_gap_ns)
    assert flags == [QualityFlag.GAP]


def test_gap_within_threshold_is_not_flagged() -> None:
    state = SequenceState()
    key = ("kis", "SKHY_000660_KRX_COMMON")
    max_gap_ns = 5_000_000_000
    state.evaluate(key, _NOW, "dedupe-1", max_gap_ns=max_gap_ns)
    flags = state.evaluate(key, _NOW + 1_000_000_000, "dedupe-2", max_gap_ns=max_gap_ns)
    assert flags == []


def test_different_keys_are_isolated() -> None:
    state = SequenceState()
    state.evaluate(("kis", "A"), _NOW, "dedupe-1")
    flags = state.evaluate(("kis", "B"), _NOW - 1_000_000_000, "dedupe-2")  # 다른 종목이면 역순 아님
    assert flags == []


def test_detect_crossed_quote_true_when_bid_above_ask() -> None:
    assert detect_crossed_quote(_quote(bid="101", ask="100")) is True


def test_detect_crossed_quote_false_for_normal_quote() -> None:
    assert detect_crossed_quote(_quote(bid="100", ask="101")) is False
