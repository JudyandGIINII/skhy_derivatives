"""P1-05 검증: SimulationClock은 항상 앞으로만 진행한다."""

from __future__ import annotations

import pytest

from skhy_research.engine.clock import ClockRewindError, SimulationClock

_T0 = 1_800_000_000_000_000_000


def test_clock_starts_at_given_time() -> None:
    clock = SimulationClock(_T0)
    assert clock.now_utc == _T0


def test_clock_advances_forward() -> None:
    clock = SimulationClock(_T0)
    clock.advance_to(_T0 + 1000)
    assert clock.now_utc == _T0 + 1000


def test_clock_allows_advancing_to_same_time() -> None:
    clock = SimulationClock(_T0)
    clock.advance_to(_T0)
    assert clock.now_utc == _T0


def test_clock_rejects_rewind() -> None:
    clock = SimulationClock(_T0)
    clock.advance_to(_T0 + 1000)
    with pytest.raises(ClockRewindError):
        clock.advance_to(_T0)
