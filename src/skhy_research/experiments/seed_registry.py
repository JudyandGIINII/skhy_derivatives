"""(strategy_version, split_name)별 seed를 추적해 다른 seed 재실행을 감지한다 (PRD 10.3).

같은 seed로 재실행(예: 재현성 검증)은 허용하지만, 다른 seed로 같은 test
구간을 다시 실행하는 것은 결과를 보고 유리한 seed를 고르는 것과 같은
효과를 낼 수 있어 차단한다.
"""

from __future__ import annotations


class DuplicateExperimentRunError(RuntimeError):
    pass


class SeedRegistry:
    def __init__(self) -> None:
        self._seeds: dict[tuple[str, str], int] = {}

    def register_run(self, strategy_version: str, split_name: str, seed: int) -> None:
        key = (strategy_version, split_name)
        existing = self._seeds.get(key)
        if existing is not None and existing != seed:
            raise DuplicateExperimentRunError(
                f"{key}는 이미 seed={existing}로 실행됐다. 다른 seed({seed})로 재실행은 "
                "차단한다(테스트 구간 재사용 위험)."
            )
        self._seeds[key] = seed

    def get_seed(self, strategy_version: str, split_name: str) -> int | None:
        return self._seeds.get((strategy_version, split_name))
