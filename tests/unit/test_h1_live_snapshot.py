"""H1 live freshness·KIS/Toss·KRX 대조 quality gate 검증."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from skhy_research.application.h1_live_snapshot import (
    KrxPreviousCloseReference,
    evaluate_live_snapshot_quality,
)
from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    MarketDataFeedMode,
    QualityFlag,
    Session,
    Venue,
)
from skhy_research.domain.market import (
    MarketPriceSnapshot,
    ObservationTimeSource,
    PublicationTimeSource,
)

_DECISION = 1_800_000_000_000_000_000


def _snapshot(
    source: str,
    price: str,
    *,
    event_time: int = _DECISION - 5_000_000_000,
    feed_mode: MarketDataFeedMode = MarketDataFeedMode.LIVE,
) -> MarketPriceSnapshot:
    return MarketPriceSnapshot(
        record_id=f"{source}-record",
        source=source,
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=event_time,
        received_time_utc=_DECISION - 1,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=feed_mode is MarketDataFeedMode.SIMULATED,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id="KRX_000660_COMMON_STOCK",
        last_price=Decimal(price),
        published_time_utc=_DECISION - 1,
        observation_time_source=ObservationTimeSource.PROVIDER_TIMESTAMP,
        publication_time_source=PublicationTimeSource.CLIENT_RECEIVED_AT,
        feed_mode=feed_mode,
    )


def _reference(price: str = "100") -> KrxPreviousCloseReference:
    return KrxPreviousCloseReference(
        instrument_id="KRX_000660_COMMON_STOCK",
        basis_date=date(2026, 7, 15),
        previous_close=Decimal(price),
        received_at_utc=_DECISION - 10_000_000_000,
        input_record_id="krx-close",
        max_live_move_pct=Decimal("30"),
    )


def test_stale_provider_timestamp_is_marked_and_blocks() -> None:
    result = evaluate_live_snapshot_quality(
        _snapshot("kis", "101", event_time=_DECISION - 120_000_000_000),
        _snapshot("toss", "101", event_time=_DECISION - 120_000_000_000),
        _reference(),
        decision_time_utc=_DECISION,
        max_snapshot_age_ns=60_000_000_000,
        max_source_time_skew_ns=5_000_000_000,
        max_cross_source_divergence_pct=Decimal("1"),
    )

    assert QualityFlag.STALE in result.flags
    assert result.blocks_signal is True


def test_cross_source_or_krx_outlier_is_marked_and_blocks() -> None:
    result = evaluate_live_snapshot_quality(
        _snapshot("kis", "150"),
        _snapshot("toss", "100"),
        _reference(),
        decision_time_utc=_DECISION,
        max_snapshot_age_ns=60_000_000_000,
        max_source_time_skew_ns=5_000_000_000,
        max_cross_source_divergence_pct=Decimal("1"),
    )

    assert QualityFlag.SOURCE_DIVERGENCE in result.flags
    assert result.live_vs_krx_move_pct == Decimal("50.0")
    assert result.blocks_signal is True


def test_vps_primary_is_blocked_even_when_prices_and_times_match() -> None:
    result = evaluate_live_snapshot_quality(
        _snapshot("kis-vps", "101", feed_mode=MarketDataFeedMode.SIMULATED),
        _snapshot("toss", "101"),
        _reference(),
        decision_time_utc=_DECISION,
        max_snapshot_age_ns=60_000_000_000,
        max_source_time_skew_ns=5_000_000_000,
        max_cross_source_divergence_pct=Decimal("1"),
    )

    assert result.flags == frozenset()
    assert result.is_live_primary is False
    assert result.blocks_signal is True
