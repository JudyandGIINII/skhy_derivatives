"""결정론적 SimulationClock. 시간은 항상 앞으로만 진행한다 (P1-05)."""

from __future__ import annotations


class ClockRewindError(RuntimeError):
    pass


class SimulationClock:
    def __init__(self, start_utc: int) -> None:
        self._current = start_utc

    @property
    def now_utc(self) -> int:
        return self._current

    def advance_to(self, event_time_utc: int) -> None:
        if event_time_utc < self._current:
            raise ClockRewindError(
                f"시계는 뒤로 갈 수 없다: current={self._current}, requested={event_time_utc}"
            )
        self._current = event_time_utc
