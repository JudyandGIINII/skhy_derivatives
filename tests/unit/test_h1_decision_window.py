"""P1-04 검증: 의사결정 윈도우가 base.yaml 설정 및 KRX 종가경매 시작과 일치한다."""

from __future__ import annotations

from datetime import date, time

from skhy_research.application.config import load_settings
from skhy_research.domain.calendar import VENUE_SESSION_SCHEDULE, local_datetime_to_utc_nanos
from skhy_research.domain.enums import Session, Venue
from skhy_research.strategies.h1_close_rebalance.decision_window import build_decision_window

_A_WEDNESDAY = date(2026, 7, 15)


def test_decision_window_matches_configured_times() -> None:
    settings = load_settings("local")
    window = build_decision_window(
        _A_WEDNESDAY, settings.h1.signal_snapshot_time_kst, settings.h1.order_intent_cutoff_kst
    )

    expected_snapshot = local_datetime_to_utc_nanos(_A_WEDNESDAY, time(15, 10, 0), Venue.KRX)
    expected_cutoff = local_datetime_to_utc_nanos(_A_WEDNESDAY, time(15, 19, 30), Venue.KRX)

    assert window.signal_snapshot_utc == expected_snapshot
    assert window.order_intent_cutoff_utc == expected_cutoff


def test_order_intent_cutoff_precedes_close_auction_start() -> None:
    settings = load_settings("local")
    window = build_decision_window(
        _A_WEDNESDAY, settings.h1.signal_snapshot_time_kst, settings.h1.order_intent_cutoff_kst
    )

    close_auction = next(w for w in VENUE_SESSION_SCHEDULE[Venue.KRX] if w.session == Session.CLOSE_AUCTION)
    close_auction_start_utc = local_datetime_to_utc_nanos(_A_WEDNESDAY, close_auction.start, Venue.KRX)

    assert window.order_intent_cutoff_utc <= close_auction_start_utc
