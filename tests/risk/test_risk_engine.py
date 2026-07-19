"""FR-14 및 PRD 11.2/11.3 리스크 엔진 수용 테스트."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from skhy_research.domain.enums import (
    ConversionStatusValue,
    OrderSide,
    RiskDecisionType,
    TimeInForce,
    Venue,
)
from skhy_research.domain.execution import OrderIntent, OrderLeg
from skhy_research.risk import (
    LegRiskState,
    MarketRiskState,
    RiskEngine,
    RiskEvaluationContext,
    RiskPolicy,
    RiskReasonCode,
    StrategyRiskClass,
    load_risk_policy,
)

_NOW = 2_000_000_000_000_000_000
_SECOND = 1_000_000_000


def _order(
    *,
    quantity: str = "10",
    risk_budget: str = "5000",
    legs: list[OrderLeg] | None = None,
) -> OrderIntent:
    resolved_legs = legs or [
        OrderLeg(
            leg_id="leg-1",
            instrument_id="KRX_000660",
            venue=Venue.KRX,
            side=OrderSide.BUY,
            quantity=Decimal(quantity),
            limit_price=Decimal("100"),
            time_in_force=TimeInForce.DAY,
        )
    ]
    return OrderIntent(
        order_id="order-1",
        signal_id="signal-1",
        strategy_id="h1_close_rebalance",
        legs=resolved_legs,
        risk_budget=Decimal(risk_budget),
        created_at_utc=_NOW - _SECOND,
        expires_at_utc=_NOW + 10 * _SECOND,
        idempotency_key="risk-engine-order-1",
    )


def _leg_state(**overrides: Any) -> LegRiskState:
    values: dict[str, Any] = {
        "quote_as_of_utc": _NOW - _SECOND,
        "stop_distance": Decimal("10"),
        "available_quantity": Decimal("1000"),
        "minimum_trade_unit": Decimal("1"),
        "market_state": MarketRiskState.OPEN,
    }
    values.update(overrides)
    return LegRiskState(**values)


def _context(**overrides: Any) -> RiskEvaluationContext:
    values: dict[str, Any] = {
        "now_utc": _NOW,
        "strategy_class": StrategyRiskClass.H1,
        "account_equity": Decimal("1000000"),
        "current_equity": Decimal("1000000"),
        "high_water_mark_equity": Decimal("1000000"),
        "daily_pnl": Decimal("0"),
        "expected_gross_return": Decimal("100"),
        "expected_cost": Decimal("10"),
        "leg_states": {"leg-1": _leg_state()},
    }
    values.update(overrides)
    return RiskEvaluationContext(**values)


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine(RiskPolicy())


def test_policy_file_matches_prd_11_2_limits() -> None:
    policy = load_risk_policy(Path(__file__).parents[2] / "configs/risk/prd_v1.yaml")

    assert policy.max_risk_per_trade_fraction == Decimal("0.0025")
    assert policy.max_daily_loss_fraction == Decimal("0.01")
    assert policy.max_cumulative_mdd_fraction == Decimal("0.05")
    assert policy.h1_quote_max_age_seconds == Decimal("2")
    assert policy.h2h3_quote_max_age_seconds == Decimal("5")


def test_allow_returns_reason_and_before_after_quantity(engine: RiskEngine) -> None:
    decision = engine.evaluate(_order(), _context())

    assert decision.decision is RiskDecisionType.ALLOW
    assert decision.reason_codes == [RiskReasonCode.WITHIN_LIMITS.value]
    assert decision.requested_quantity == Decimal("10")
    assert decision.approved_quantity == Decimal("10")


def test_risk_budget_uses_most_conservative_trade_limit(engine: RiskEngine) -> None:
    decision = engine.evaluate(
        _order(quantity="100", risk_budget="1000"),
        _context(account_equity=Decimal("100000")),
    )

    assert decision.decision is RiskDecisionType.REDUCE
    assert RiskReasonCode.MAX_RISK_PER_TRADE.value in decision.reason_codes
    assert decision.requested_quantity == Decimal("100")
    assert decision.approved_quantity == Decimal("25")
    assert decision.limits_snapshot["max_trade_risk"] == Decimal("250")


def test_order_budget_liquidity_and_lot_size_reduce_quantity(engine: RiskEngine) -> None:
    state = _leg_state(available_quantity=Decimal("21"), minimum_trade_unit=Decimal("6"))
    decision = engine.evaluate(
        _order(quantity="100", risk_budget="230"),
        _context(leg_states={"leg-1": state}),
    )

    assert decision.decision is RiskDecisionType.REDUCE
    assert decision.approved_quantity == Decimal("18")
    assert RiskReasonCode.ORDER_RISK_BUDGET.value in decision.reason_codes
    assert RiskReasonCode.LIQUIDITY_LIMIT.value in decision.reason_codes
    assert RiskReasonCode.MINIMUM_LOT_ROUNDING.value in decision.reason_codes


def test_multi_leg_order_blocks_if_one_leg_cannot_retain_executable_quantity(
    engine: RiskEngine,
) -> None:
    legs = [
        OrderLeg(
            leg_id=leg_id,
            instrument_id=leg_id,
            venue=Venue.KRX if leg_id == "common" else Venue.NASDAQ,
            side=OrderSide.BUY if leg_id == "common" else OrderSide.SELL,
            quantity=Decimal(quantity),
            limit_price=Decimal("100"),
            time_in_force=TimeInForce.DAY,
        )
        for leg_id, quantity in (("common", "10"), ("adr", "1"))
    ]
    context = _context(
        strategy_class=StrategyRiskClass.H2,
        conversion_status=ConversionStatusValue.OPERATIONAL,
        leg_states={
            "common": _leg_state(),
            "adr": _leg_state(available_quantity=Decimal("0.5")),
        },
    )

    decision = engine.evaluate(_order(legs=legs), context)

    assert decision.decision is RiskDecisionType.BLOCK
    assert decision.approved_quantity == Decimal("0")
    assert RiskReasonCode.NO_EXECUTABLE_QUANTITY.value in decision.reason_codes


@pytest.mark.parametrize(
    ("strategy_class", "quote_age_seconds", "expected"),
    [
        (StrategyRiskClass.H1, Decimal("2"), RiskDecisionType.ALLOW),
        (StrategyRiskClass.H1, Decimal("2.000000001"), RiskDecisionType.BLOCK),
        (StrategyRiskClass.H2, Decimal("5"), RiskDecisionType.ALLOW),
        (StrategyRiskClass.H2, Decimal("5.000000001"), RiskDecisionType.BLOCK),
        (StrategyRiskClass.H3, Decimal("5"), RiskDecisionType.ALLOW),
    ],
)
def test_strategy_quote_age_boundaries(
    engine: RiskEngine,
    strategy_class: StrategyRiskClass,
    quote_age_seconds: Decimal,
    expected: RiskDecisionType,
) -> None:
    quote_age_nanos = int(quote_age_seconds * _SECOND)
    context = _context(
        strategy_class=strategy_class,
        conversion_status=(
            ConversionStatusValue.OPERATIONAL
            if strategy_class is StrategyRiskClass.H2
            else None
        ),
        leg_states={"leg-1": _leg_state(quote_as_of_utc=_NOW - quote_age_nanos)},
    )

    decision = engine.evaluate(_order(), context)

    assert decision.decision is expected
    if expected is RiskDecisionType.BLOCK:
        assert RiskReasonCode.STALE_QUOTE.value in decision.reason_codes


@pytest.mark.parametrize(
    ("context_override", "reason"),
    [
        ({"critical_providers_connected": False}, RiskReasonCode.CRITICAL_PROVIDER_DISCONNECTED),
        ({"critical_providers_delayed": True}, RiskReasonCode.CRITICAL_PROVIDER_DELAYED),
        ({"provider_divergence": True}, RiskReasonCode.PROVIDER_DIVERGENCE),
        ({"clock_synchronized": False}, RiskReasonCode.CLOCK_UNSYNCHRONIZED),
        ({"daily_pnl": Decimal("-10000")}, RiskReasonCode.DAILY_LOSS_LIMIT),
        ({"current_equity": Decimal("950000")}, RiskReasonCode.CUMULATIVE_MDD_LIMIT),
        (
            {"expected_cost": Decimal("100")},
            RiskReasonCode.EXPECTED_COST_NOT_BELOW_GROSS,
        ),
        ({"hedge_failed": True}, RiskReasonCode.HEDGE_FAILURE),
        (
            {"unhedged_since_utc": _NOW - 5 * _SECOND},
            RiskReasonCode.UNHEDGED_LEG_TIMEOUT,
        ),
        ({"strategy_version_approved": False}, RiskReasonCode.STRATEGY_VERSION_MISMATCH),
        ({"config_version_approved": False}, RiskReasonCode.CONFIG_VERSION_MISMATCH),
        (
            {"data_schema_version_approved": False},
            RiskReasonCode.DATA_SCHEMA_VERSION_MISMATCH,
        ),
    ],
)
def test_global_kill_switches_block_new_order(
    engine: RiskEngine,
    context_override: dict[str, Any],
    reason: RiskReasonCode,
) -> None:
    decision = engine.evaluate(_order(), _context(**context_override))

    assert decision.decision is RiskDecisionType.BLOCK
    assert decision.approved_quantity == Decimal("0")
    assert reason.value in decision.reason_codes


@pytest.mark.parametrize(
    ("state_override", "reason"),
    [
        ({"market_state": MarketRiskState.CLOSED}, RiskReasonCode.MARKET_CLOSED),
        ({"market_state": MarketRiskState.HALTED}, RiskReasonCode.MARKET_HALTED),
        ({"market_state": MarketRiskState.VI}, RiskReasonCode.MARKET_VI),
        ({"market_state": MarketRiskState.PRICE_LIMIT}, RiskReasonCode.MARKET_PRICE_LIMIT),
        ({"market_state": MarketRiskState.UNKNOWN}, RiskReasonCode.MARKET_STATE_UNKNOWN),
        ({"quote_as_of_utc": None}, RiskReasonCode.QUOTE_MISSING),
        ({"stop_distance": None}, RiskReasonCode.STOP_DISTANCE_MISSING),
        ({"available_quantity": None}, RiskReasonCode.LIQUIDITY_MISSING),
        (
            {"requires_borrow": True, "borrow_available": False},
            RiskReasonCode.BORROW_UNAVAILABLE,
        ),
        ({"requires_borrow": True}, RiskReasonCode.BORROW_COST_MISSING),
        (
            {
                "requires_borrow": True,
                "borrow_cost_expires_at_utc": _NOW,
            },
            RiskReasonCode.BORROW_COST_EXPIRED,
        ),
    ],
)
def test_leg_kill_switches_block_new_order(
    engine: RiskEngine,
    state_override: dict[str, Any],
    reason: RiskReasonCode,
) -> None:
    context = _context(leg_states={"leg-1": _leg_state(**state_override)})

    decision = engine.evaluate(_order(), context)

    assert decision.decision is RiskDecisionType.BLOCK
    assert reason.value in decision.reason_codes


def test_h2_requires_operational_conversion(engine: RiskEngine) -> None:
    context = _context(
        strategy_class=StrategyRiskClass.H2,
        conversion_status=ConversionStatusValue.SUSPENDED,
    )

    decision = engine.evaluate(_order(), context)

    assert decision.decision is RiskDecisionType.BLOCK
    assert RiskReasonCode.CONVERSION_UNAVAILABLE.value in decision.reason_codes


def test_missing_leg_state_is_fail_closed(engine: RiskEngine) -> None:
    decision = engine.evaluate(_order(), _context(leg_states={}))

    assert decision.decision is RiskDecisionType.BLOCK
    assert RiskReasonCode.LEG_STATE_MISSING.value in decision.reason_codes


def test_duplicate_leg_identifier_is_fail_closed(engine: RiskEngine) -> None:
    leg = _order().legs[0]
    duplicate = leg.model_copy(update={"quantity": Decimal("5")})

    decision = engine.evaluate(_order(legs=[leg, duplicate]), _context())

    assert decision.decision is RiskDecisionType.BLOCK
    assert decision.requested_quantity == Decimal("15")
    assert RiskReasonCode.DUPLICATE_LEG_ID.value in decision.reason_codes
