"""P0-11 검증: 코드의 GATE_DEFINITIONS와 docs/decisions/gates/*.md가 어긋나지 않는다."""

from __future__ import annotations

from pathlib import Path

from skhy_research.application.gate_registry import GATE_DEFINITIONS

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
