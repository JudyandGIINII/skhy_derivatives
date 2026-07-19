"""P1-07 검증: 시간순 60/30/30 split과 확장형 walk-forward (PRD 10.3)."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from skhy_research.experiments.splits import chronological_split, walk_forward_splits


def _trading_days(n: int, start: date = date(2026, 1, 2)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def test_chronological_split_produces_60_30_30_boundaries() -> None:
    days = _trading_days(120)
    splits = chronological_split(days, train_days=60, validation_days=30, test_days=30)

    train, validation, test = splits
    assert train.name == "train"
    assert train.start == days[0]
    assert train.end == days[59]
    assert validation.start == days[60]
    assert validation.end == days[89]
    assert test.start == days[90]
    assert test.end == days[119]


def test_chronological_split_requires_minimum_days() -> None:
    days = _trading_days(100)
    with pytest.raises(ValueError, match="거래일이 부족"):
        chronological_split(days, train_days=60, validation_days=30, test_days=30)


def test_chronological_split_sorts_unordered_input() -> None:
    days = _trading_days(120)
    shuffled = days.copy()
    random.Random(1).shuffle(shuffled)

    assert chronological_split(shuffled, 60, 30, 30) == chronological_split(days, 60, 30, 30)


def test_walk_forward_splits_produce_expanding_anchored_windows() -> None:
    days = _trading_days(200)
    splits = walk_forward_splits(days, initial_train_days=60, step_days=30, test_days=30)

    # step 0..3: test_end_idx = 60+30 .. 60+3*30+30=180 <= 200 모두 통과, step4=210>200 중단
    assert len(splits) == 8  # 4 스텝 * (train+test)

    train_1, test_1, train_2 = splits[0], splits[1], splits[2]
    assert train_1.name == "walk_forward_1_train"
    assert train_1.start == days[0]
    assert train_1.end == days[59]
    assert test_1.name == "walk_forward_1_test"
    assert test_1.start == days[60]
    assert test_1.end == days[89]

    # train은 앵커드(확장형): 항상 day[0]부터 시작하고 뒤로 갈수록 길어진다
    assert train_2.start == days[0]
    assert train_2.end == days[89]  # 60 + 1*30 - 1


def test_walk_forward_test_windows_do_not_overlap_across_steps() -> None:
    days = _trading_days(200)
    splits = walk_forward_splits(days, initial_train_days=60, step_days=30, test_days=30)
    test_splits = [s for s in splits if "test" in s.name]

    for earlier, later in zip(test_splits, test_splits[1:], strict=False):
        assert earlier.end < later.start


def test_walk_forward_returns_empty_when_insufficient_days() -> None:
    days = _trading_days(50)
    assert walk_forward_splits(days, initial_train_days=60, step_days=30, test_days=30) == []
