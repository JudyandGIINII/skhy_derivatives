"""P1-08 검증: PRD 10.5 핵심 지표 계산."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from skhy_research.experiments.statistics import (
    TradeResult,
    bootstrap_confidence_interval,
    compute_expectancy,
    compute_max_drawdown,
    compute_median_trade_pnl,
    compute_profit_factor,
    compute_win_rate,
    date_permutation_p_value,
    top_n_day_profit_share,
)

_D = date(2026, 1, 2)


def _trades(pnls: list[str]) -> list[TradeResult]:
    return [TradeResult(f"t{i}", Decimal(p), _D) for i, p in enumerate(pnls)]


def test_expectancy_is_mean_pnl() -> None:
    trades = _trades(["100", "-50", "200"])
    assert compute_expectancy(trades) == Decimal("250") / 3


def test_expectancy_of_empty_trades_is_zero() -> None:
    assert compute_expectancy([]) == Decimal("0")


def test_median_trade_pnl_odd_count() -> None:
    trades = _trades(["10", "-5", "20"])
    assert compute_median_trade_pnl(trades) == Decimal("10")


def test_median_trade_pnl_even_count() -> None:
    trades = _trades(["10", "20", "-5", "30"])  # sorted: -5,10,20,30 -> mid avg (10+20)/2
    assert compute_median_trade_pnl(trades) == Decimal("15")


def test_win_rate_counts_positive_pnl_only() -> None:
    trades = _trades(["10", "-5", "0", "20"])  # 0은 승리로 세지 않음
    assert compute_win_rate(trades) == Decimal("2") / Decimal("4")


def test_profit_factor_ratio() -> None:
    trades = _trades(["100", "50", "-60", "-40"])
    assert compute_profit_factor(trades) == Decimal("150") / Decimal("100")


def test_profit_factor_infinite_when_no_losses() -> None:
    trades = _trades(["100", "50"])
    assert compute_profit_factor(trades) == Decimal("Infinity")


def test_profit_factor_zero_when_no_trades_profit_or_loss() -> None:
    assert compute_profit_factor([]) == Decimal("0")


def test_max_drawdown_tracks_peak_to_trough() -> None:
    # 누적: 100, 150, 90, 120 -> peak 150에서 90까지 낙폭 60
    trades = _trades(["100", "50", "-60", "30"])
    assert compute_max_drawdown(trades) == Decimal("60")


def test_max_drawdown_zero_when_monotonically_increasing() -> None:
    trades = _trades(["10", "20", "30"])
    assert compute_max_drawdown(trades) == Decimal("0")


def test_top_n_day_profit_share() -> None:
    daily = {
        date(2026, 1, 1): Decimal("100"),
        date(2026, 1, 2): Decimal("50"),
        date(2026, 1, 3): Decimal("-30"),  # 손실일은 분모에서 제외
        date(2026, 1, 4): Decimal("20"),
    }
    # 총 양의 이익 = 170, 최고 1일 = 100 -> 100/170
    assert top_n_day_profit_share(daily, n=1) == Decimal("100") / Decimal("170")


def test_top_n_day_profit_share_zero_when_no_positive_days() -> None:
    daily = {date(2026, 1, 1): Decimal("-10")}
    assert top_n_day_profit_share(daily, n=1) == Decimal("0")


def test_bootstrap_confidence_interval_is_deterministic_given_seed() -> None:
    values = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")]
    ci_a = bootstrap_confidence_interval(values, n_resamples=200, confidence=0.9, seed=7)
    ci_b = bootstrap_confidence_interval(values, n_resamples=200, confidence=0.9, seed=7)
    assert ci_a == ci_b


def test_bootstrap_confidence_interval_brackets_sample_mean_roughly() -> None:
    values = [Decimal("10")] * 50  # 상수 데이터: CI가 사실상 10 근처로 붕괴
    lower, upper = bootstrap_confidence_interval(values, n_resamples=200, confidence=0.9, seed=1)
    assert lower <= Decimal("10") <= upper


def test_date_permutation_p_value_within_unit_interval() -> None:
    daily = {date(2026, 1, i + 1): Decimal(str(v)) for i, v in enumerate([10, -5, 8, -3, 12])}
    p = date_permutation_p_value(daily, n_permutations=200, seed=3)
    assert 0.0 <= p <= 1.0


def test_date_permutation_p_value_is_deterministic_given_seed() -> None:
    daily = {date(2026, 1, i + 1): Decimal(str(v)) for i, v in enumerate([10, -5, 8, -3, 12])}
    p_a = date_permutation_p_value(daily, n_permutations=200, seed=3)
    p_b = date_permutation_p_value(daily, n_permutations=200, seed=3)
    assert p_a == p_b
