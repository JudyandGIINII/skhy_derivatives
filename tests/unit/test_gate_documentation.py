"""P0-11 검증: 코드의 GATE_DEFINITIONS와 docs/decisions/gates/*.md가 어긋나지 않는다."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from skhy_research.application.gate_registry import GATE_DEFINITIONS, GateRegistry
from skhy_research.domain.gate import GateDecision, GateStatus

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATES_DIR = _REPO_ROOT / "docs" / "decisions" / "gates"


def test_every_defined_gate_has_a_decision_record_file() -> None:
    for gate_id in GATE_DEFINITIONS:
        doc_path = _GATES_DIR / f"{gate_id}.md"
        assert doc_path.exists(), f"{doc_path} 없음"


def test_decision_record_starts_unknown_and_names_the_gate() -> None:
    for gate_id in GATE_DEFINITIONS:
        content = (_GATES_DIR / f"{gate_id}.md").read_text(encoding="utf-8")
        assert content.startswith(f"# {gate_id}")
        assert "`UNKNOWN`" in content


def test_decision_record_documents_default_block_action() -> None:
    for gate_id, definition in GATE_DEFINITIONS.items():
        content = (_GATES_DIR / f"{gate_id}.md").read_text(encoding="utf-8")
        assert "미확인 시 기본동작" in content
        # 미확인 기본동작 문구의 핵심 키워드가 문서에도 그대로 남아 있어야 한다(코드-문서 drift 방지)
        first_keyword = definition.default_action_if_unresolved.split(",")[0].split(" ")[0]
        assert first_keyword in content


def test_g04_confirmed_document_evidence_passes_registry_validation() -> None:
    content = (_GATES_DIR / "G-04.md").read_text(encoding="utf-8")
    evidence_path = _GATES_DIR / "evidence" / "G-04-h1-required-fields.md"
    checksum = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    confirmed_at = _epoch_nanos("2026-07-18T12:26:33Z")
    valid_until = _epoch_nanos("2026-10-16T12:26:33Z")

    assert "| 상태 | `CONFIRMED` |" in content
    assert GATE_DEFINITIONS["G-04"].question in content
    assert GATE_DEFINITIONS["G-04"].default_action_if_unresolved in content
    assert checksum == "5e962fab2172e3d55712f670c1706487e6568008187f2e77a55a573cc14285d1"
    assert checksum in content
    assert "2026-07-18T12:26:33Z" in content
    assert "2026-10-16T12:26:33Z" in content

    registry = GateRegistry()
    registry.record_decision(
        GateDecision(
            gate_id="G-04",
            status=GateStatus.CONFIRMED,
            evidence_url="https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd",
            evidence_checksum=checksum,
            responsible_provider="KRX Open API",
            conclusion="무료 KRX 일별 H1 universe·listed-notional proxy·가용성·lineage 계약 확인",
            confirmed_at_utc=confirmed_at,
            valid_until_utc=valid_until,
            recorded_at_utc=confirmed_at,
        )
    )
    assert registry.blocks("G-04", confirmed_at) is False


def _epoch_nanos(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1_000_000_000)
