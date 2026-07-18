"""거래 기록에서 PRD 10.5 핵심 지표를 계산한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import numpy as np


@dataclass(frozen=True)
class TradeResult:
    trade_id: str
    pnl: Decimal  # 비용 후 손익
    trading_date: date


def compute_expectancy(trades: list[TradeResult]) -> Decimal:
    if not trades:
        return Decimal("0")
    return sum((t.pnl for t in trades), Decimal("0")) / len(trades)


def compute_median_trade_pnl(trades: list[TradeResult]) -> Decimal:
    if not trades:
        return Decimal("0")
    sorted_pnls = sorted(t.pnl for t in trades)
    mid = len(sorted_pnls) // 2
    if len(sorted_pnls) % 2 == 1:
        return sorted_pnls[mid]
    return (sorted_pnls[mid - 1] + sorted_pnls[mid]) / 2


def compute_win_rate(trades: list[TradeResult]) -> Decimal:
    if not trades:
        return Decimal("0")
    wins = sum(1 for t in trades if t.pnl > 0)
    return Decimal(wins) / Decimal(len(trades))


def compute_profit_factor(trades: list[TradeResult]) -> Decimal:
    gross_profit = sum((t.pnl for t in trades if t.pnl > 0), Decimal("0"))
    gross_loss = sum((-t.pnl for t in trades if t.pnl < 0), Decimal("0"))
    if gross_loss == 0:
        return Decimal("Infinity") if gross_profit > 0 else Decimal("0")
    return gross_profit / gross_loss


def compute_max_drawdown(trades_in_time_order: list[TradeResult]) -> Decimal:
    """trades_in_time_order는 호출자가 시간순으로 정렬해서 전달해야 한다."""
    cumulative = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    for t in trades_in_time_order:
        cumulative += t.pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd


def top_n_day_profit_share(daily_pnls: dict[date, Decimal], n: int) -> Decimal:
    positive = [p for p in daily_pnls.values() if p > 0]
    total_positive = sum(positive, Decimal("0"))
    if total_positive == 0:
        return Decimal("0")
    top_n_sum = sum(sorted(positive, reverse=True)[:n], Decimal("0"))
    return top_n_sum / total_positive


def bootstrap_confidence_interval(
    values: list[Decimal], n_resamples: int, confidence: float, seed: int
) -> tuple[Decimal, Decimal]:
    if not values:
        return Decimal("0"), Decimal("0")
    rng = np.random.default_rng(seed)
    arr = np.array([float(v) for v in values])
    means = np.empty(n_resamples)
    for i in range(n_resamples):
        sample = rng.choice(arr, size=len(arr), replace=True)
        means[i] = sample.mean()
    alpha = (1 - confidence) / 2
    lower = float(np.quantile(means, alpha))
    upper = float(np.quantile(means, 1 - alpha))
    return Decimal(str(lower)), Decimal(str(upper))


def date_permutation_p_value(
    daily_pnls: dict[date, Decimal], n_permutations: int, seed: int
) -> float:
    """일별 부호를 무작위로 뒤섞었을 때 관측된 평균 이상이 나올 확률(단측 p-value)."""
    values = np.array([float(v) for v in daily_pnls.values()])
    if len(values) == 0:
        return 1.0
    observed_mean = values.mean()
    rng = np.random.default_rng(seed)
    count_ge = 0
    for _ in range(n_permutations):
        signs = rng.choice([-1.0, 1.0], size=len(values))
        permuted_mean = (np.abs(values) * signs).mean()
        if permuted_mean >= observed_mean:
            count_ge += 1
    return count_ge / n_permutations
