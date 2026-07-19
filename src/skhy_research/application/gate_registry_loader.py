"""영속 GateDecision으로 런타임 GateRegistry를 구성한다.

Markdown gate 문서는 사람용 검토 기록이고 런타임 입력이 아니다. PostgreSQL store가
반환한 기계용 결정만 `GateRegistry.record_decision()`을 거쳐 로드하므로, 불완전한
CONFIRMED 결정은 URL·checksum·결론·담당·시각 검증 단계에서 즉시 거부된다.
"""

from __future__ import annotations

from typing import Protocol

from skhy_research.application.gate_registry import GateRegistry
from skhy_research.domain.gate import GateDecision


class GateDecisionReader(Protocol):
    def load_all_decisions(self) -> list[GateDecision]: ...


def load_gate_registry(store: GateDecisionReader) -> GateRegistry:
    """저장소의 gate별 최신 결정을 검증해 새 registry로 로드한다."""
    registry = GateRegistry()
    for decision in store.load_all_decisions():
        registry.record_decision(decision)
    return registry
