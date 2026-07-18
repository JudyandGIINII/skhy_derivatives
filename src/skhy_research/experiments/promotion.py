"""전략 승격 판정 (PRD 10.6). 모든 조건을 만족해야 PASS.

표본 부족으로 판정 불가능하면 HOLD, 기준을 실패하면 REJECT다. 임계값을
낮춰 실패한 전략을 사후 구제하지 않는다 — 이는 `SplitContaminationGuard`
(P1-07)와 결합해 test 관측 후 같은 버전으로 기준을 재조정하지 못하게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from skhy_research.domain.enums import PromotionVerdict


@dataclass(frozen=True)
class PromotionCriteria:
    min_expectancy: Decimal
    min_profit_factor: Decimal
    stress_min_cumulative_pnl: Decimal
    max_single_day_profit_share: Decimal
    max_strategy_mdd_pct: Decimal
    min_sample_size: int


@dataclass(frozen=True)
class PromotionInput:
    trade_count: int
    expectancy: Decimal
    profit_factor: Decimal
    stress_cumulative_pnl: Decimal
    top_1_day_profit_share: Decimal
    mdd_pct: Decimal


@dataclass(frozen=True)
class PromotionResult:
    verdict: PromotionVerdict
    reasons: tuple[str, ...]


def evaluate_promotion(data: PromotionInput, criteria: PromotionCriteria) -> PromotionResult:
    if data.trade_count < criteria.min_sample_size:
        return PromotionResult(
            PromotionVerdict.HOLD,
            (f"표본 부족: trade_count={data.trade_count} < min={criteria.min_sample_size}",),
        )

    failures: list[str] = []
    if data.expectancy <= criteria.min_expectancy:
        failures.append(f"expectancy={data.expectancy} <= min={criteria.min_expectancy}")
    if data.profit_factor < criteria.min_profit_factor:
        failures.append(f"profit_factor={data.profit_factor} < min={criteria.min_profit_factor}")
    if data.stress_cumulative_pnl < criteria.stress_min_cumulative_pnl:
        failures.append(
            f"stress_cumulative_pnl={data.stress_cumulative_pnl} < "
            f"min={criteria.stress_min_cumulative_pnl}"
        )
    if data.top_1_day_profit_share > criteria.max_single_day_profit_share:
        failures.append(
            f"top_1_day_profit_share={data.top_1_day_profit_share} > "
            f"max={criteria.max_single_day_profit_share}"
        )
    if data.mdd_pct > criteria.max_strategy_mdd_pct:
        failures.append(f"mdd_pct={data.mdd_pct} > max={criteria.max_strategy_mdd_pct}")

    if failures:
        return PromotionResult(PromotionVerdict.REJECT, tuple(failures))
    return PromotionResult(PromotionVerdict.PASS, ())
