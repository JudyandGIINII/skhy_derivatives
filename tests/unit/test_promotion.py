"""P1-08 검증: PASS/HOLD/REJECT 판정이 PRD 10.6 기준을 정확히 따른다."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from skhy_research.application.config import load_settings
from skhy_research.domain.enums import PromotionVerdict
from skhy_research.experiments.promotion import (
    PromotionCriteria,
    PromotionInput,
    evaluate_promotion,
)


def _criteria(min_sample_size: int = 30) -> PromotionCriteria:
    settings = load_settings("local")
    p = settings.promotion
    return PromotionCriteria(
        min_expectancy=Decimal(str(p.min_expectancy)),
        min_profit_factor=Decimal(str(p.min_profit_factor)),
        stress_min_cumulative_pnl=Decimal(str(p.stress_min_cumulative_pnl)),
        max_single_day_profit_share=Decimal(str(p.max_single_day_profit_share)),
        max_strategy_mdd_pct=Decimal(str(p.max_strategy_mdd_pct)),
        min_sample_size=min_sample_size,
    )


def _passing_input(**overrides: Any) -> PromotionInput:
    base: dict[str, Any] = dict(
        trade_count=50,
        expectancy=Decimal("100"),
        profit_factor=Decimal("1.5"),
        stress_cumulative_pnl=Decimal("500"),
        top_1_day_profit_share=Decimal("0.20"),
        mdd_pct=Decimal("3.0"),
    )
    base.update(overrides)
    return PromotionInput(**base)


def test_insufficient_sample_size_yields_hold() -> None:
    result = evaluate_promotion(_passing_input(trade_count=10), _criteria(min_sample_size=30))
    assert result.verdict == PromotionVerdict.HOLD
    assert "표본 부족" in result.reasons[0]


def test_all_criteria_met_yields_pass() -> None:
    result = evaluate_promotion(_passing_input(), _criteria())
    assert result.verdict == PromotionVerdict.PASS
    assert result.reasons == ()


def test_expectancy_at_or_below_minimum_rejects() -> None:
    result = evaluate_promotion(_passing_input(expectancy=Decimal("0")), _criteria())
    assert result.verdict == PromotionVerdict.REJECT
    assert any("expectancy" in r for r in result.reasons)


def test_profit_factor_below_minimum_rejects() -> None:
    result = evaluate_promotion(_passing_input(profit_factor=Decimal("1.1")), _criteria())
    assert result.verdict == PromotionVerdict.REJECT
    assert any("profit_factor" in r for r in result.reasons)


def test_negative_stress_pnl_rejects() -> None:
    result = evaluate_promotion(_passing_input(stress_cumulative_pnl=Decimal("-1")), _criteria())
    assert result.verdict == PromotionVerdict.REJECT
    assert any("stress_cumulative_pnl" in r for r in result.reasons)


def test_excessive_single_day_concentration_rejects() -> None:
    result = evaluate_promotion(_passing_input(top_1_day_profit_share=Decimal("0.5")), _criteria())
    assert result.verdict == PromotionVerdict.REJECT
    assert any("top_1_day_profit_share" in r for r in result.reasons)


def test_excessive_mdd_rejects() -> None:
    result = evaluate_promotion(_passing_input(mdd_pct=Decimal("6.0")), _criteria())
    assert result.verdict == PromotionVerdict.REJECT
    assert any("mdd_pct" in r for r in result.reasons)


def test_multiple_failures_are_all_reported_not_just_first() -> None:
    result = evaluate_promotion(
        _passing_input(expectancy=Decimal("-1"), profit_factor=Decimal("0.5")), _criteria()
    )
    assert result.verdict == PromotionVerdict.REJECT
    assert len(result.reasons) == 2


def test_criteria_match_base_yaml_promotion_config() -> None:
    criteria = _criteria()
    assert criteria.min_profit_factor == Decimal("1.2")
    assert criteria.max_strategy_mdd_pct == Decimal("5.0")
    assert criteria.max_single_day_profit_share == Decimal("0.3")


def test_ineligible_daily_proxy_is_held_even_when_metrics_pass() -> None:
    result = evaluate_promotion(
        _passing_input(
            model_version="h1_krx_daily_proxy_reduced_v1",
            data_resolution="daily-proxy",
            promotion_scope="h1-daily-proxy-research-only",
            promotion_eligible=False,
        ),
        _criteria(),
    )

    assert result.verdict == PromotionVerdict.HOLD
    assert "승격 비대상" in result.reasons[0]
    assert result.model_version == "h1_krx_daily_proxy_reduced_v1"
    assert result.data_resolution == "daily-proxy"
    assert result.promotion_scope == "h1-daily-proxy-research-only"
    assert result.promotion_eligible is False
