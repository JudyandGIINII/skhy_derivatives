"""Round 3 Huber 회귀·target·검증 gate의 fixture-only 계약."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from skhy_research.application.h1_continuous_ofi_proxy import (
    ELASTIC_NET_LAMBDAS,
    ELASTIC_NET_RHOS,
    HUBER_DELTA_MULTIPLIERS,
    CalibrationStatus,
    HuberElasticNetHyperparameters,
    PaperFill,
    PassGateMetrics,
    RegressionSample,
    RegressionStatus,
    RequiredCostEvidence,
    SourceGateEvidence,
    TargetStatus,
    ValidationStage,
    build_close_return_target,
    calculate_theoretical_z,
    evaluate_calibration_reliability,
    evaluate_cheap_reject,
    evaluate_final_pass_gate,
    evaluate_source_gate,
    fit_direction_calibration,
    fit_fixed_huber_elastic_net,
    fit_sealed_huber_elastic_net,
    summarize_target_distribution,
)
from skhy_research.domain.enums import PromotionVerdict

_START = date(2026, 1, 2)


def _sample(index: int, *, target: Decimal | None = None) -> RegressionSample:
    x = Decimal(index - 15) / Decimal("10")
    resolved_target = target if target is not None else Decimal("0.003") * x
    return RegressionSample(
        trading_date=_START + timedelta(days=index),
        theoretical_z={"FUND_A": Decimal((index % 5) - 2) / Decimal("100")},
        flow_features={
            "x_ofi": x,
            "x_depth": Decimal(index % 7) / Decimal("10"),
            "x_micro": Decimal((index * 3) % 11) / Decimal("10"),
            "x_program": Decimal((index * 5) % 13) / Decimal("10"),
            "x_conflict": Decimal(index % 2),
        },
        target_return=resolved_target,
    )


def test_target_uses_two_sided_executable_fill_center() -> None:
    result = build_close_return_target(
        PaperFill(Decimal("101"), 10, 10, 100),
        PaperFill(Decimal("99"), 10, 10, 100),
        official_close_price=Decimal("102"),
        official_close_available_at_utc=200,
        entry_deadline_utc=102,
        outcome_deadline_utc=210,
    )

    assert result.status is TargetStatus.COMPUTABLE
    assert result.p_entry_ref == Decimal("100")
    assert result.value == Decimal("0.02")


def test_one_sided_fill_missing_is_not_a_zero_target() -> None:
    result = build_close_return_target(
        PaperFill(Decimal("101"), 10, 10, 100),
        PaperFill(None, 0, 10, None, "SELL_DEPTH_INSUFFICIENT"),
        official_close_price=Decimal("102"),
        official_close_available_at_utc=200,
        entry_deadline_utc=102,
        outcome_deadline_utc=210,
    )

    assert result.status is TargetStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.p_entry_ref is None
    assert result.missing_reasons == ("SELL_DEPTH_INSUFFICIENT",)


def test_theoretical_z_uses_prd_beta_formula_and_adv_normalization() -> None:
    result = calculate_theoretical_z(
        beta=Decimal("2"),
        prior_nav=Decimal("1000000"),
        underlying_return=Decimal("0.01"),
        underlying_20d_adv_notional=Decimal("100000000"),
    )
    assert result == Decimal("0.0002")


def test_fixed_l1_fit_records_theory_term_elimination() -> None:
    samples = [_sample(index) for index in range(40)]
    result = fit_fixed_huber_elastic_net(
        samples,
        HuberElasticNetHyperparameters(1.35, 1.0, 1.0),
    )

    assert result.status is RegressionStatus.FITTED
    assert result.model is not None
    assert result.model.selected_from_train_only is True
    assert "THEORY_TERM_ELIMINATED:FUND_A" in result.model.flags
    assert result.model.coefficients["z:FUND_A"] == 0


def test_grid_selection_is_closed_inside_train_and_uses_all_sealed_candidates() -> None:
    samples = [_sample(index) for index in range(30)]
    result = fit_sealed_huber_elastic_net(samples)

    assert result.status is RegressionStatus.FITTED
    assert result.model is not None
    assert result.model.train_dates == tuple(sample.trading_date for sample in samples)
    assert len(result.candidate_scores) == (
        len(HUBER_DELTA_MULTIPLIERS) * len(ELASTIC_NET_LAMBDAS) * len(ELASTIC_NET_RHOS)
    )
    assert result.model.selected_from_train_only is True


def test_zero_target_scale_and_missing_target_are_not_fitted_as_zero() -> None:
    constant = [_sample(index, target=Decimal("0.01")) for index in range(30)]
    fit = fit_sealed_huber_elastic_net(constant)
    missing_sample = RegressionSample(
        trading_date=_START,
        theoretical_z={"FUND_A": Decimal("0.1")},
        flow_features={},
        target_return=None,
        target_missing_reason="BUY_FILL_MISSING",
    )
    diagnostics = summarize_target_distribution((missing_sample, _sample(1)))

    assert fit.status is RegressionStatus.NOT_COMPUTABLE
    assert fit.reason == "TARGET_SCALE_ZERO"
    assert diagnostics.scheduled_count == 2
    assert diagnostics.computable_count == 1
    assert diagnostics.missing_count == 1
    assert diagnostics.missing_reason_counts == {"BUY_FILL_MISSING": 1}


def test_confidence_is_null_when_direction_calibration_is_not_identified() -> None:
    result = fit_direction_calibration(
        [Decimal(index) for index in range(10)],
        [Decimal("0.01")] * 10,
    )
    assert result.status is CalibrationStatus.CALIBRATION_NOT_IDENTIFIED
    assert result.confidence is None
    assert result.brier_score is None


def test_calibration_is_post_hoc_reliability_diagnostic_only() -> None:
    scores = [Decimal(index - 10) / Decimal("10") for index in range(20)]
    targets = [Decimal("-0.01")] * 10 + [Decimal("0.01")] * 10
    fitted = fit_direction_calibration(scores, targets)
    evaluation = evaluate_calibration_reliability(fitted.confidence, scores, targets)

    assert fitted.status is CalibrationStatus.IDENTIFIED
    assert fitted.usage == "POST_HOC_DIAGNOSTIC_ONLY"
    assert evaluation.usage == "POST_HOC_DIAGNOSTIC_ONLY"
    assert evaluation.brier_score is not None
    assert sum(item.count for item in evaluation.reliability_curve) == 20


def test_required_cost_table_distinguishes_stock_not_applicable_from_missing_cost() -> None:
    stock_costs = RequiredCostEvidence(
        commission_return=Decimal("0.0001"),
        tax_return=Decimal("0.0018"),
        spread_return=Decimal("0.0002"),
        slippage_return=Decimal("0.0001"),
        market_impact_return=Decimal("0.0001"),
        product_and_tracking_return=None,
    )
    missing_market_impact = RequiredCostEvidence(
        commission_return=Decimal("0.0001"),
        tax_return=Decimal("0.0018"),
        spread_return=Decimal("0.0002"),
        slippage_return=Decimal("0.0001"),
        market_impact_return=None,
        product_and_tracking_return=None,
    )

    assert stock_costs.is_complete is True
    base_total = stock_costs.base_total_return
    assert base_total is not None
    assert stock_costs.double_stress_total_return == base_total * 2
    assert missing_market_impact.is_complete is False
    assert missing_market_impact.base_total_return is None


def test_sanitized_fixture_source_and_all_downstream_gates_remain_hold() -> None:
    source = evaluate_source_gate(SourceGateEvidence())
    cheap = evaluate_cheap_reject(
        source,
        [Decimal("100")] * 30,
        permutations=20,
    )
    passing_shaped_metrics = PassGateMetrics(
        eligible_trading_days=200,
        eligible_signal_count=100,
        expectancy=Decimal("100"),
        profit_factor=Decimal("2"),
        mdd_fraction_of_fixed_capital=Decimal("0.01"),
        top_single_day_positive_profit_share=Decimal("0.1"),
        stress_cumulative_pnl=Decimal("1000"),
        block_bootstrap_expectancy_ci_lower=Decimal("1"),
        permutation_p_value=Decimal("0.01"),
        sealed_test_incremental_net_pnl=Decimal("100"),
        sealed_test_complete=True,
        walk_forward_block_count=2,
    )
    final = evaluate_final_pass_gate(source, cheap, passing_shaped_metrics)

    assert source.verdict is PromotionVerdict.HOLD
    assert "LIVE_SOURCE_NOT_CAPTURED" in source.reasons
    assert cheap.verdict is PromotionVerdict.HOLD
    assert final.verdict is PromotionVerdict.HOLD
    assert final.stage is ValidationStage.FINAL_PASS_GATE
