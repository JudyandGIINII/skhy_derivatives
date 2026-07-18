"""여러 공급자 수집 task를 격리 실행한다 (P0-10, PRD 13.2 "장애 격리").

한 task의 예외가 다른 task 실행을 막지 않는다. 결과는 성공/실패 모두 보존한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class IsolatedResult[T]:
    ok: bool
    value: T | None = None
    error: str | None = None


def run_isolated[T](tasks: dict[str, Callable[[], T]]) -> dict[str, IsolatedResult[T]]:
    results: dict[str, IsolatedResult[T]] = {}
    for name, task in tasks.items():
        try:
            results[name] = IsolatedResult(ok=True, value=task())
        except Exception as exc:  # noqa: BLE001 - 한 task의 실패가 다른 task를 막아선 안 된다
            results[name] = IsolatedResult(ok=False, error=str(exc))
    return results
