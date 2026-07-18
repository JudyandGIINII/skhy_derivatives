"""test 구간 봉인 (PRD 10.3): test 관측 후에는 같은 전략버전·test 구간의 파라미터를 바꿀 수 없다.

변경하려면 새 strategy_version과 미사용 test 구간을 확보해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class SplitContaminationError(RuntimeError):
    pass


@dataclass
class SplitContaminationGuard:
    _sealed: set[tuple[str, str]] = field(default_factory=set)

    def seal_after_test_observation(self, strategy_version: str, test_split_name: str) -> None:
        self._sealed.add((strategy_version, test_split_name))

    def assert_can_tune(self, strategy_version: str, test_split_name: str) -> None:
        if (strategy_version, test_split_name) in self._sealed:
            raise SplitContaminationError(
                f"strategy_version={strategy_version}, test_split={test_split_name}는 이미 test "
                "구간을 관측했다. 파라미터를 바꾸려면 새 strategy_version과 미사용 test 구간이 필요하다."
            )
