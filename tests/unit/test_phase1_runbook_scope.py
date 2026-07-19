"""daily-proxy를 원 15:10 H1 완료로 승격해 읽을 수 없도록 문구를 고정한다."""

from pathlib import Path


def test_phase1_runbook_separates_proxy_and_original_h1_completion() -> None:
    runbook = (
        Path(__file__).parents[2] / "docs/runbooks/phase1-completion-check.md"
    ).read_text(encoding="utf-8")

    assert "daily-proxy(`h1_krx_daily_proxy_reduced_v1`) 경로 완료" in runbook
    assert "원 15:10 H1\n> 백테스트·리스크·승격 경로 미완료" in runbook
    assert "PRD Phase 1 완료나 원 H1의 PASS/HOLD/REJECT 판정으로 승격할 수 없다" in runbook
