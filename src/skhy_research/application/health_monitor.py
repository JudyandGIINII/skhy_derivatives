"""공급자별 건강상태·수신지연·재연결·누락 보충 추적 (P0-10, PRD 13.2).

한 공급자의 상태는 다른 공급자와 완전히 분리된 키(port_type, name)로 관리되어
"한 공급자의 장애가 다른 수집기와 저장 계층을 중단시키지 않는다"는 요구를
데이터 구조 단계에서부터 보장한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ProviderKey = tuple[str, str]  # (port_type, provider_name)


@dataclass
class ProviderHealthState:
    is_connected: bool = False
    last_event_at_utc: int | None = None
    measured_latency_ms: float | None = None
    reconnect_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    backfilled_gaps: list[tuple[int, int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "is_connected": self.is_connected,
            "last_event_at_utc": self.last_event_at_utc,
            "measured_latency_ms": self.measured_latency_ms,
            "reconnect_count": self.reconnect_count,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "backfilled_gap_count": len(self.backfilled_gaps),
        }


class HealthMonitor:
    def __init__(self) -> None:
        self._states: dict[ProviderKey, ProviderHealthState] = {}

    def _state(self, port_type: str, provider_name: str) -> ProviderHealthState:
        key = (port_type, provider_name)
        if key not in self._states:
            self._states[key] = ProviderHealthState()
        return self._states[key]

    def record_event(
        self, port_type: str, provider_name: str, event_time_utc: int, latency_ms: float
    ) -> None:
        state = self._state(port_type, provider_name)
        state.is_connected = True
        state.last_event_at_utc = event_time_utc
        state.measured_latency_ms = latency_ms
        state.consecutive_failures = 0

    def record_failure(self, port_type: str, provider_name: str, error: str) -> None:
        state = self._state(port_type, provider_name)
        state.is_connected = False
        state.consecutive_failures += 1
        state.last_error = error

    def record_reconnect(self, port_type: str, provider_name: str) -> None:
        state = self._state(port_type, provider_name)
        state.reconnect_count += 1
        state.is_connected = True
        state.consecutive_failures = 0

    def record_backfill(
        self, port_type: str, provider_name: str, gap_start_utc: int, gap_end_utc: int
    ) -> None:
        state = self._state(port_type, provider_name)
        state.backfilled_gaps.append((gap_start_utc, gap_end_utc))

    def snapshot(self) -> dict[ProviderKey, ProviderHealthState]:
        return dict(self._states)

    def is_isolated_failure(self, failed_key: ProviderKey) -> bool:
        """실패한 공급자를 제외한 나머지가 모두 정상 연결 상태인지 확인한다."""
        others = [state for key, state in self._states.items() if key != failed_key]
        return all(state.is_connected for state in others)
