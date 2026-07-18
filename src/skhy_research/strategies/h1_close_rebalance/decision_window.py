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


def build_decision_window(
    trading_date: date, signal_snapshot_time_kst: str, order_intent_cutoff_kst: str
) -> H1DecisionWindow:
    snapshot_time = time.fromisoformat(signal_snapshot_time_kst)
    cutoff_time = time.fromisoformat(order_intent_cutoff_kst)
    return H1DecisionWindow(
        signal_snapshot_utc=local_datetime_to_utc_nanos(trading_date, snapshot_time, Venue.KRX),
        order_intent_cutoff_utc=local_datetime_to_utc_nanos(trading_date, cutoff_time, Venue.KRX),
    )
