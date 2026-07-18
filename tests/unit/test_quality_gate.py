"""P0-09 검증: QualityGate가 품질 플래그를 합성해 신호 차단 여부를 판정한다 (FR-05)."""

from __future__ import annotations

from decimal import Decimal

from skhy_research.data.quality.quality_gate import QualityGate
from skhy_research.domain.enums import AdjustmentStatus, Currency, QualityFlag, Session, Venue
from skhy_research.domain.market import MarketQuote

_NOW = 1_800_000_000_000_000_000


def _quote(
    source: str = "kis",
    bid: str = "100",
    ask: str = "101",
    event_time_utc: int = _NOW,
    quality_flag: list[QualityFlag] | None = None,
) -> MarketQuote:
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
        instrument_id="SKHY_000660_KRX_COMMON",
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=Decimal("10"),
        ask_size=Decimal("10"),
        quality_flag=quality_flag or [],
    )


def test_normal_quote_does_not_block_signal() -> None:
    gate = QualityGate()
    evaluation = gate.evaluate_quote(_quote(), dedupe_key="dedupe-1")
    assert evaluation.blocks_signal is False
    assert evaluation.is_crossed_quote is False


def test_duplicate_resubmission_blocks_signal() -> None:
    gate = QualityGate()
    gate.evaluate_quote(_quote(), dedupe_key="dedupe-1")
    evaluation = gate.evaluate_quote(_quote(), dedupe_key="dedupe-1")
    assert QualityFlag.DUPLICATE in evaluation.flags
    assert evaluation.blocks_signal is True


def test_gap_blocks_signal() -> None:
    gate = QualityGate(max_gap_ns=5_000_000_000)
    gate.evaluate_quote(_quote(event_time_utc=_NOW), dedupe_key="dedupe-1")
    evaluation = gate.evaluate_quote(
        _quote(event_time_utc=_NOW + 10_000_000_000), dedupe_key="dedupe-2"
    )

    assert QualityFlag.GAP in evaluation.flags
    assert evaluation.blocks_signal is True


def test_crossed_quote_blocks_signal_even_without_explicit_flag() -> None:
    gate = QualityGate()
    evaluation = gate.evaluate_quote(_quote(bid="101", ask="100"), dedupe_key="dedupe-1")
    assert evaluation.is_crossed_quote is True
    assert evaluation.blocks_signal is True


def test_cross_source_divergence_blocks_signal() -> None:
    gate = QualityGate()
    primary = _quote(source="kis", bid="99999", ask="100001")
    secondary = _quote(source="toss", bid="101999", ask="102001")  # ~2% 괴리

    evaluation = gate.evaluate_cross_source(
        primary, secondary, tolerance_pct=Decimal("0.5"), max_time_skew_ns=5_000_000_000
    )
    assert QualityFlag.SOURCE_DIVERGENCE in evaluation.flags
    assert evaluation.blocks_signal is True


def test_cross_source_within_tolerance_does_not_block() -> None:
    gate = QualityGate()
    primary = _quote(source="kis", bid="99999", ask="100001")
    secondary = _quote(source="toss", bid="100019", ask="100021")  # ~0.02% 차이

    evaluation = gate.evaluate_cross_source(
        primary, secondary, tolerance_pct=Decimal("0.5"), max_time_skew_ns=5_000_000_000
    )
    assert evaluation.blocks_signal is False


def test_cross_source_time_skew_marks_stale_and_blocks() -> None:
    gate = QualityGate()
    primary = _quote(source="kis", event_time_utc=_NOW)
    secondary = _quote(source="toss", event_time_utc=_NOW + 10_000_000_000)

    evaluation = gate.evaluate_cross_source(
        primary,
        secondary,
        tolerance_pct=Decimal("0.5"),
        max_time_skew_ns=5_000_000_000,
    )

    assert QualityFlag.STALE in evaluation.flags
    assert evaluation.blocks_signal is True


def test_stale_reference_blocks_signal() -> None:
    gate = QualityGate()
    quote = _quote(event_time_utc=_NOW)
    evaluation = gate.evaluate_staleness(quote, as_of_utc=_NOW + 10_000_000_000, max_age_ns=2_000_000_000)
    assert QualityFlag.STALE in evaluation.flags
    assert evaluation.blocks_signal is True


def test_fresh_reference_does_not_block_signal() -> None:
    gate = QualityGate()
    quote = _quote(event_time_utc=_NOW)
    evaluation = gate.evaluate_staleness(quote, as_of_utc=_NOW + 500_000_000, max_age_ns=2_000_000_000)
    assert evaluation.blocks_signal is False
