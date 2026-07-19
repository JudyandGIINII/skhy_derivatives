"""P0-10 검증: 한 수집 task의 예외가 다른 task 실행을 막지 않는다 (PRD 13.2)."""

from __future__ import annotations

from skhy_research.application.isolated_task_runner import run_isolated


def test_one_failing_task_does_not_block_others() -> None:
    executed: list[str] = []

    def ok_task() -> str:
        executed.append("ok")
        return "success"

    def failing_task() -> str:
        executed.append("failing")
        raise RuntimeError("provider down")

    results = run_isolated({"kis": ok_task, "toss": failing_task, "krx": ok_task})

    assert set(executed) == {"ok", "failing"}  # 두 task 모두 실행됨(순서 무관)
    assert results["kis"].ok is True
    assert results["kis"].value == "success"
    assert results["toss"].ok is False
    assert "provider down" in (results["toss"].error or "")
    assert results["krx"].ok is True


def test_all_tasks_can_fail_independently() -> None:
    def fail_a() -> None:
        raise ValueError("a broke")

    def fail_b() -> None:
        raise ValueError("b broke")

    results = run_isolated({"a": fail_a, "b": fail_b})

    assert results["a"].ok is False
    assert results["b"].ok is False
    assert results["a"].error != results["b"].error
