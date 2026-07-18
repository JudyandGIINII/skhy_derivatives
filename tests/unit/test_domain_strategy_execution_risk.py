"""P0-04 검증: Signal/OrderIntent/PaperFill/RiskDecision/ExperimentResult 불변조건."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from skhy_research.domain.enums import (
    OrderSide,
    OrderStatus,
    PromotionVerdict,
    RiskDecisionType,
    SignalDirection,
    TimeInForce,
    Venue,
)
from skhy_research.domain.execution import OrderIntent, OrderLeg, PaperFill
from skhy_research.domain.experiment import ExperimentResult
from skhy_research.domain.risk import RiskDecision
from skhy_research.domain.strategy import Signal

_NOW = 1_800_000_000_000_000_000


def _signal(**overrides: Any) -> Signal:
    base: dict[str, Any] = dict(
        signal_id="sig-1",
        strategy_id="h1_close_rebalance",
        strategy_version="1.0.0",
        instrument_id="000660",
        direction=SignalDirection.LONG,
        confidence=Decimal("0.7"),
        expected_gross_return=Decimal("0.004"),
        expected_cost=Decimal("0.001"),
        expected_net_return=Decimal("0.003"),
        generated_at_utc=_NOW,
        expires_at_utc=_NOW + 60_000_000_000,
        feature_set_id="h1_close_pressure@1.0.0",
    )
    base.update(overrides)
    return Signal(**base)


def test_signal_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        _signal(confidence=Decimal("1.5"))


def test_signal_rejects_inconsistent_net_return() -> None:
    with pytest.raises(ValidationError, match="expected_net_return"):
        _signal(expected_net_return=Decimal("0.999"))


def test_signal_rejects_expiry_before_generation() -> None:
    with pytest.raises(ValidationError, match="expires_at_utc"):
        _signal(expires_at_utc=_NOW - 1)


def test_signal_valid_construction() -> None:
    signal = _signal()
    assert signal.direction == SignalDirection.LONG


def _leg(**overrides: Any) -> OrderLeg:
    base: dict[str, Any] = dict(
        leg_id="leg-1",
        instrument_id="000660",
        venue=Venue.KRX,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        limit_price=Decimal("200000"),
        time_in_force=TimeInForce.DAY,
    )
    base.update(overrides)
    return OrderLeg(**base)


def test_order_intent_requires_at_least_one_leg() -> None:
    with pytest.raises(ValidationError, match="leg"):
        OrderIntent(
            order_id="order-1",
            signal_id="sig-1",
            strategy_id="h1_close_rebalance",
            legs=[],
            risk_budget=Decimal("1000000"),
            created_at_utc=_NOW,
            expires_at_utc=_NOW + 1,
            idempotency_key="idem-1",
        )


def test_order_intent_rejects_expiry_before_creation() -> None:
    with pytest.raises(ValidationError, match="expires_at_utc"):
        OrderIntent(
            order_id="order-1",
            signal_id="sig-1",
            strategy_id="h1_close_rebalance",
            legs=[_leg()],
            risk_budget=Decimal("1000000"),
            created_at_utc=_NOW,
            expires_at_utc=_NOW - 1,
            idempotency_key="idem-1",
        )


def test_order_intent_valid_construction() -> None:
    order = OrderIntent(
        order_id="order-1",
        signal_id="sig-1",
        strategy_id="h1_close_rebalance",
        legs=[_leg()],
        risk_budget=Decimal("1000000"),
        created_at_utc=_NOW,
        expires_at_utc=_NOW + 60_000_000_000,
        idempotency_key="idem-1",
    )
    assert len(order.legs) == 1


def test_paper_fill_valid_construction() -> None:
    fill = PaperFill(
        fill_id="fill-1",
        order_id="order-1",
        leg_id="leg-1",
        filled_quantity=Decimal("10"),
        unfilled_quantity=Decimal("0"),
        fill_price=Decimal("200100"),
        used_market_event_ids=["evt-1", "evt-2"],
        slippage=Decimal("100"),
        fill_model_version="h1_close_auction@1.0.0",
        filled_at_utc=_NOW,
        status=OrderStatus.FILLED,
    )
    assert fill.status == OrderStatus.FILLED


def _risk_decision(**overrides: Any) -> RiskDecision:
    base: dict[str, Any] = dict(
        decision_id="risk-1",
        order_intent_id="order-1",
        decision=RiskDecisionType.ALLOW,
        reason_codes=[],
        requested_quantity=Decimal("10"),
        approved_quantity=Decimal("10"),
        limits_snapshot={"max_risk_per_trade_pct": Decimal("0.25")},
        decided_at_utc=_NOW,
    )
    base.update(overrides)
    return RiskDecision(**base)


def test_risk_decision_block_requires_zero_approved_quantity() -> None:
    with pytest.raises(ValidationError, match="BLOCK"):
        _risk_decision(
            decision=RiskDecisionType.BLOCK,
            reason_codes=["SOURCE_DIVERGENCE"],
            approved_quantity=Decimal("5"),
        )


def test_risk_decision_block_requires_reason_codes() -> None:
    with pytest.raises(ValidationError, match="사유코드"):
        _risk_decision(
            decision=RiskDecisionType.BLOCK,
            reason_codes=[],
            approved_quantity=Decimal("0"),
        )


def test_risk_decision_approved_cannot_exceed_requested() -> None:
    with pytest.raises(ValidationError, match="approved_quantity"):
        _risk_decision(
            decision=RiskDecisionType.REDUCE,
            reason_codes=["RISK_BUDGET"],
            requested_quantity=Decimal("10"),
            approved_quantity=Decimal("15"),
        )


def test_risk_decision_allow_valid_construction() -> None:
    decision = _risk_decision()
    assert decision.decision == RiskDecisionType.ALLOW


def test_experiment_result_valid_construction() -> None:
    result = ExperimentResult(
        experiment_id="exp-1",
        run_id="run-1",
        strategy_id="h1_close_rebalance",
        strategy_version="1.0.0",
        data_snapshot_id="snap-1",
        split_name="test",
        cost_scenario="base",
        metrics={"expectancy": Decimal("0.002"), "profit_factor": Decimal("1.35")},
        confidence_intervals={"expectancy": (Decimal("0.001"), Decimal("0.003"))},
        verdict=PromotionVerdict.PASS,
        verdict_reason="모든 10.6 기준 충족",
        created_at_utc=_NOW,
    )
    assert result.verdict == PromotionVerdict.PASS


def test_experiment_result_rejects_pass_for_promotion_ineligible_proxy() -> None:
    with pytest.raises(ValidationError, match="PASS"):
        ExperimentResult(
            experiment_id="exp-proxy",
            run_id="run-proxy",
            strategy_id="h1_close_rebalance",
            strategy_version="h1_krx_daily_proxy_reduced_v1",
            data_snapshot_id="snap-proxy",
            split_name="test",
            cost_scenario="base",
            verdict=PromotionVerdict.PASS,
            verdict_reason="proxy 성과를 원 H1 PASS로 오기록",
            created_at_utc=_NOW,
            model_version="h1_krx_daily_proxy_reduced_v1",
            data_resolution="daily-proxy",
            promotion_scope="h1-daily-proxy-research-only",
            promotion_eligible=False,
        )
