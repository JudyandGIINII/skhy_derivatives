"""확정 gate 결정 seed 소스 검증.

- author된 evidence checksum이 실제 evidence 파일과 일치하는지 (문서-코드 drift 방지)
- seed 결정이 GateRegistry.record_decision의 CONFIRMED 검증을 통과하는지
- 멱등 seed가 반복 실행 시 중복 저장하지 않는지
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from skhy_research.application.gate_decision_seed import (
    confirmed_seed_checksums,
    confirmed_seed_decisions,
    confirmed_seed_evidence_relpaths,
    seed_confirmed_gate_decisions,
)
from skhy_research.application.gate_registry import GateRegistry
from skhy_research.domain.gate import GateDecision, GateStatus

_REPO_ROOT = Path(__file__).resolve().parents[2]
# 2026-09-01T00:00:00Z: 모든 seed confirmed_at(2026-07-18) 이후이고
# 가장 이른 valid_until(G-02/G-04 2026-10-16) 이전 → CONFIRMED 유효 구간.
_RECORDED_AT = 1_788_220_800_000_000_000


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[GateDecision] = []

    def save_decision(self, decision: GateDecision) -> None:
        self.rows.append(decision)

    def load_all_decisions(self) -> list[GateDecision]:
        latest: dict[str, GateDecision] = {}
        for row in self.rows:
            current = latest.get(row.gate_id)
            if current is None or row.recorded_at_utc > current.recorded_at_utc:
                latest[row.gate_id] = row
        return list(latest.values())


def test_seed_checksums_match_evidence_files() -> None:
    relpaths = confirmed_seed_evidence_relpaths()
    checksums = confirmed_seed_checksums()
    for gate_id, relpath in relpaths.items():
        actual = hashlib.sha256((_REPO_ROOT / relpath).read_bytes()).hexdigest()
        assert actual == checksums[gate_id], f"{gate_id} evidence checksum drift: {relpath}"


def test_seed_decisions_pass_confirmed_registry_validation() -> None:
    registry = GateRegistry()
    for decision in confirmed_seed_decisions(_RECORDED_AT):
        assert decision.status == GateStatus.CONFIRMED
        registry.record_decision(decision)  # 검증 실패 시 예외
    for gate_id in ("G-02", "G-04", "G-06"):
        assert registry.is_resolved(gate_id, _RECORDED_AT)
        assert registry.blocks(gate_id, _RECORDED_AT) is False


def test_seed_is_idempotent_on_repeat() -> None:
    store = _FakeStore()

    first = seed_confirmed_gate_decisions(store, recorded_at_utc=_RECORDED_AT)
    assert {o.action for o in first} == {"inserted"}
    assert len(store.rows) == 3

    second = seed_confirmed_gate_decisions(store, recorded_at_utc=_RECORDED_AT + 1)
    assert {o.action for o in second} == {"already-current"}
    assert len(store.rows) == 3  # 중복 저장 없음
