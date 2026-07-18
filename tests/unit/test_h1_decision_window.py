"""P1-04 검증: 의사결정 윈도우가 base.yaml 설정 및 KRX 종가경매 시작과 일치한다."""

from __future__ import annotations

from datetime import date, time

import pytest

from skhy_research.application.config import load_settings
from skhy_research.domain.calendar import VENUE_SESSION_SCHEDULE, local_datetime_to_utc_nanos
from skhy_research.domain.enums import Session, Venue
from skhy_research.strategies.h1_close_rebalance.decision_window import (
    H1DecisionWindowError,
    assert_order_intent_cutoff,
    build_decision_window,
)

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


@pytest.mark.parametrize(
    ("snapshot", "cutoff"),
    (("15:09:59", "15:19:30"), ("15:10:00", "15:19:31")),
)
def test_original_h1_rejects_any_non_prd_decision_window(snapshot: str, cutoff: str) -> None:
    with pytest.raises(H1DecisionWindowError):
        build_decision_window(_A_WEDNESDAY, snapshot, cutoff)


def test_order_intent_must_expire_at_exact_151930_cutoff() -> None:
    window = build_decision_window(_A_WEDNESDAY, "15:10:00", "15:19:30")

    with pytest.raises(H1DecisionWindowError, match="정확히"):
        assert_order_intent_cutoff(window, window.order_intent_cutoff_utc + 1)
