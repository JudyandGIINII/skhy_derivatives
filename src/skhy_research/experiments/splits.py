"""시간순 데이터 분할과 확장형 walk-forward (PRD 10.3, FR-01).

무작위 shuffle은 절대 사용하지 않는다. 시간순만 허용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TimeSplit:
    name: str  # train|validation|test|walk_forward_<n>_train|walk_forward_<n>_test
    start: date
    end: date  # inclusive


def chronological_split(
    trading_days: list[date], train_days: int, validation_days: int, test_days: int
) -> list[TimeSplit]:
    total_needed = train_days + validation_days + test_days
    if len(trading_days) < total_needed:
        raise ValueError(f"거래일이 부족하다: 필요 {total_needed}, 보유 {len(trading_days)}")

    sorted_days = sorted(trading_days)
    train = sorted_days[:train_days]
    validation = sorted_days[train_days : train_days + validation_days]
    test = sorted_days[train_days + validation_days : train_days + validation_days + test_days]

    return [
        TimeSplit("train", train[0], train[-1]),
        TimeSplit("validation", validation[0], validation[-1]),
        TimeSplit("test", test[0], test[-1]),
    ]


def walk_forward_splits(
    trading_days: list[date], initial_train_days: int, step_days: int, test_days: int
) -> list[TimeSplit]:
    """앵커드(확장형) walk-forward. train은 매 스텝 처음부터 누적 확장되고,

    test는 그 직후 test_days만큼 이동한다.
    """
    sorted_days = sorted(trading_days)
    splits: list[TimeSplit] = []
    step = 0
    while True:
        train_end_idx = initial_train_days + step * step_days
        test_start_idx = train_end_idx
        test_end_idx = test_start_idx + test_days
        if test_end_idx > len(sorted_days):
            break
        splits.append(
            TimeSplit(
                f"walk_forward_{step + 1}_train", sorted_days[0], sorted_days[train_end_idx - 1]
            )
        )
        splits.append(
            TimeSplit(
                f"walk_forward_{step + 1}_test",
                sorted_days[test_start_idx],
                sorted_days[test_end_idx - 1],
            )
        )
        step += 1
    return splits
