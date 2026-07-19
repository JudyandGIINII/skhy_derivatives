"""백테스트 이벤트 정렬 계약 (P1-05, PRD 10.1, `implementation_plan.md` 4.5).

같은 `available_time_utc`의 이벤트는 이 규칙으로 결정론적으로 정렬된다.
규칙 자체에 버전을 부여해(`ORDERING_VERSION`) 향후 규칙이 바뀌면 이전
실행과 재현성 비교 대상에서 구분한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ORDERING_VERSION = "1.0.0"

_VENUE_PRIORITY: dict[str, int] = {
    "KRX": 0,
    "NXT": 1,
    "NASDAQ": 2,
    "HKEX": 3,
    "OTC": 4,
    "REFERENCE": 5,
}
_EVENT_TYPE_RANK: dict[str, int] = {
    "reference": 0,
    "bar": 1,
    "quote": 2,
    "trade": 3,
    "timer": 4,
}
_NO_SEQUENCE_SENTINEL = 2**62  # provider sequence가 없는 이벤트는 항상 마지막으로 정렬


@dataclass(frozen=True)
class SimulationEvent:
    event_id: str
    available_time_utc: int  # 시스템이 알 수 있게 된 시각 — 1차 정렬키
    event_time_utc: int
    venue: str
    event_type: str  # "quote"|"trade"|"bar"|"reference"|"timer"
    provider_sequence: int | None
    payload: Any

    def sort_key(self) -> tuple[int, int, int, int, int, str]:
        return (
            self.available_time_utc,
            self.event_time_utc,
            self.provider_sequence if self.provider_sequence is not None else _NO_SEQUENCE_SENTINEL,
            _VENUE_PRIORITY.get(self.venue, 99),
            _EVENT_TYPE_RANK.get(self.event_type, 99),
            self.event_id,
        )


def sort_events(events: list[SimulationEvent]) -> list[SimulationEvent]:
    return sorted(events, key=lambda e: e.sort_key())
