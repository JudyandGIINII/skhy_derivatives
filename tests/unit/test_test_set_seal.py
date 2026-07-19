"""P1-07 검증: test 구간 관측 후 같은 버전으로 파라미터를 바꾸지 못한다."""

from __future__ import annotations

import pytest

from skhy_research.experiments.test_set_seal import SplitContaminationError, SplitContaminationGuard


def test_tuning_allowed_before_test_is_observed() -> None:
    guard = SplitContaminationGuard()
    guard.assert_can_tune("1.0.0", "test")  # 예외 없이 통과


def test_tuning_blocked_after_test_observation_for_same_version() -> None:
    guard = SplitContaminationGuard()
    guard.seal_after_test_observation("1.0.0", "test")

    with pytest.raises(SplitContaminationError, match="1.0.0"):
        guard.assert_can_tune("1.0.0", "test")


def test_new_strategy_version_is_not_blocked() -> None:
    guard = SplitContaminationGuard()
    guard.seal_after_test_observation("1.0.0", "test")

    guard.assert_can_tune("1.1.0", "test")  # 새 버전이면 허용


def test_different_test_split_is_not_blocked() -> None:
    guard = SplitContaminationGuard()
    guard.seal_after_test_observation("1.0.0", "test")

    guard.assert_can_tune("1.0.0", "walk_forward_2_test")  # 미사용 test 구간이면 허용
