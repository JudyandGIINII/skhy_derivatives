"""H1 의사결정 시각 계산 (PRD 9.1: 15:10 KST snapshot, 15:19:30 주문 의도 마감)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from skhy_research.domain.calendar import local_datetime_to_utc_nanos
from skhy_research.domain.enums import Venue


@dataclass(frozen=True)
class H1DecisionWindow:
    signal_snapshot_utc: int
    order_intent_cutoff_utc: int


class H1DecisionWindowError(ValueError):
    """15:10 snapshot부터 order intent cutoff 사이가 아닌 실시간 판단을 차단한다."""


def build_decision_window(
    trading_date: date, signal_snapshot_time_kst: str, order_intent_cutoff_kst: str
) -> H1DecisionWindow:
    snapshot_time = time.fromisoformat(signal_snapshot_time_kst)
    cutoff_time = time.fromisoformat(order_intent_cutoff_kst)
    return H1DecisionWindow(
        signal_snapshot_utc=local_datetime_to_utc_nanos(trading_date, snapshot_time, Venue.KRX),
        order_intent_cutoff_utc=local_datetime_to_utc_nanos(trading_date, cutoff_time, Venue.KRX),
    )


def assert_live_decision_time(window: H1DecisionWindow, decision_time_utc: int) -> None:
    if window.order_intent_cutoff_utc <= window.signal_snapshot_utc:
        raise H1DecisionWindowError("order intent cutoff은 snapshot 시각보다 늦어야 한다")
    if not (window.signal_snapshot_utc <= decision_time_utc <= window.order_intent_cutoff_utc):
        raise H1DecisionWindowError(
            "live decision_time이 15:10 snapshot~order intent cutoff 범위 밖이다: "
            f"snapshot={window.signal_snapshot_utc}, decision={decision_time_utc}, "
            f"cutoff={window.order_intent_cutoff_utc}"
        )
