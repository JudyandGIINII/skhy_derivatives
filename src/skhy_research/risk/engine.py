"""OrderIntent 전체를 fail-closed로 판정하는 FR-14 리스크 엔진."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from skhy_research.domain.enums import ConversionStatusValue, RiskDecisionType
from skhy_research.domain.execution import OrderIntent
from skhy_research.domain.risk import RiskDecision
from skhy_research.risk.models import (
    LegRiskState,
    MarketRiskState,
    RiskEvaluationContext,
    RiskPolicy,
    RiskReasonCode,
    StrategyRiskClass,
)

_NANOS_PER_SECOND = Decimal("1000000000")
_MARKET_BLOCK_REASON = {
    MarketRiskState.CLOSED: RiskReasonCode.MARKET_CLOSED,
    MarketRiskState.HALTED: RiskReasonCode.MARKET_HALTED,
    MarketRiskState.VI: RiskReasonCode.MARKET_VI,
    MarketRiskState.PRICE_LIMIT: RiskReasonCode.MARKET_PRICE_LIMIT,
    MarketRiskState.UNKNOWN: RiskReasonCode.MARKET_STATE_UNKNOWN,
}


def _append_unique(reasons: list[RiskReasonCode], reason: RiskReasonCode) -> None:
    if reason not in reasons:
        reasons.append(reason)


class RiskEngine:
    """PRD 11.2 한도와 11.3 킬스위치를 순수 계산으로 적용한다."""

    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self.policy = policy or RiskPolicy()

    def evaluate(
        self, order_intent: OrderIntent, context: RiskEvaluationContext
    ) -> RiskDecision:
        requested_by_leg = {leg.leg_id: Decimal(leg.quantity) for leg in order_intent.legs}
        requested_quantity = sum(
            (Decimal(leg.quantity) for leg in order_intent.legs), start=Decimal("0")
        )
        reasons: list[RiskReasonCode] = []
        limits = self._base_limits(context)
        if len(requested_by_leg) != len(order_intent.legs):
            _append_unique(reasons, RiskReasonCode.DUPLICATE_LEG_ID)
        for leg_id, quantity in requested_by_leg.items():
            limits[f"requested_quantity:{leg_id}"] = quantity

        leg_states = self._apply_kill_switches(order_intent, context, reasons, limits)
        if reasons:
            return self._decision(
                order_intent,
                context,
                RiskDecisionType.BLOCK,
                reasons,
                requested_quantity,
                Decimal("0"),
                limits,
            )

        return self._size_order(
            order_intent,
            context,
            leg_states,
            requested_by_leg,
            requested_quantity,
            limits,
        )

    def _apply_kill_switches(
        self,
        order: OrderIntent,
        context: RiskEvaluationContext,
        reasons: list[RiskReasonCode],
        limits: dict[str, Decimal],
    ) -> dict[str, LegRiskState]:
        if context.now_utc >= order.expires_at_utc:
            _append_unique(reasons, RiskReasonCode.ORDER_EXPIRED)
        if context.account_equity <= 0 or context.high_water_mark_equity <= 0:
            _append_unique(reasons, RiskReasonCode.INVALID_ACCOUNT_EQUITY)
        if not context.critical_providers_connected:
            _append_unique(reasons, RiskReasonCode.CRITICAL_PROVIDER_DISCONNECTED)
        if context.critical_providers_delayed:
            _append_unique(reasons, RiskReasonCode.CRITICAL_PROVIDER_DELAYED)
        if context.provider_divergence:
            _append_unique(reasons, RiskReasonCode.PROVIDER_DIVERGENCE)
        if not context.clock_synchronized:
            _append_unique(reasons, RiskReasonCode.CLOCK_UNSYNCHRONIZED)

        if context.account_equity > 0:
            daily_loss_fraction = max(-context.daily_pnl, Decimal("0")) / context.account_equity
            limits["daily_loss_fraction"] = daily_loss_fraction
            if daily_loss_fraction >= self.policy.max_daily_loss_fraction:
                _append_unique(reasons, RiskReasonCode.DAILY_LOSS_LIMIT)
        if context.high_water_mark_equity > 0:
            drawdown = max(
                context.high_water_mark_equity - context.current_equity, Decimal("0")
            )
            mdd_fraction = drawdown / context.high_water_mark_equity
            limits["cumulative_mdd_fraction"] = mdd_fraction
            if mdd_fraction >= self.policy.max_cumulative_mdd_fraction:
                _append_unique(reasons, RiskReasonCode.CUMULATIVE_MDD_LIMIT)

        if context.expected_cost >= context.expected_gross_return:
            _append_unique(reasons, RiskReasonCode.EXPECTED_COST_NOT_BELOW_GROSS)
        if not context.strategy_version_approved:
            _append_unique(reasons, RiskReasonCode.STRATEGY_VERSION_MISMATCH)
        if not context.config_version_approved:
            _append_unique(reasons, RiskReasonCode.CONFIG_VERSION_MISMATCH)
        if not context.data_schema_version_approved:
            _append_unique(reasons, RiskReasonCode.DATA_SCHEMA_VERSION_MISMATCH)

        if context.strategy_class is StrategyRiskClass.H2 and (
            context.conversion_status is not ConversionStatusValue.OPERATIONAL
        ):
            _append_unique(reasons, RiskReasonCode.CONVERSION_UNAVAILABLE)
        if context.hedge_failed:
            _append_unique(reasons, RiskReasonCode.HEDGE_FAILURE)
        if context.unhedged_since_utc is not None:
            unhedged_age = Decimal(context.now_utc - context.unhedged_since_utc) / _NANOS_PER_SECOND
            limits["unhedged_leg_age_seconds"] = unhedged_age
            if unhedged_age >= self.policy.leg_timeout_seconds:
                _append_unique(reasons, RiskReasonCode.UNHEDGED_LEG_TIMEOUT)

        quote_max_age = self._quote_max_age(context.strategy_class)
        leg_states: dict[str, LegRiskState] = {}
        for leg in order.legs:
            state = context.leg_states.get(leg.leg_id)
            if state is None:
                _append_unique(reasons, RiskReasonCode.LEG_STATE_MISSING)
                continue
            leg_states[leg.leg_id] = state
            market_reason = _MARKET_BLOCK_REASON.get(state.market_state)
            if market_reason is not None:
                _append_unique(reasons, market_reason)
            if state.quote_as_of_utc is None:
                _append_unique(reasons, RiskReasonCode.QUOTE_MISSING)
            else:
                quote_age = Decimal(context.now_utc - state.quote_as_of_utc) / _NANOS_PER_SECOND
                limits[f"quote_age_seconds:{leg.leg_id}"] = quote_age
                if quote_age < 0:
                    _append_unique(reasons, RiskReasonCode.QUOTE_FROM_FUTURE)
                elif quote_age > quote_max_age:
                    _append_unique(reasons, RiskReasonCode.STALE_QUOTE)
            if state.stop_distance is None or state.stop_distance <= 0:
                _append_unique(reasons, RiskReasonCode.STOP_DISTANCE_MISSING)
            if state.available_quantity is None:
                _append_unique(reasons, RiskReasonCode.LIQUIDITY_MISSING)
            if state.requires_borrow:
                if not state.borrow_available:
                    _append_unique(reasons, RiskReasonCode.BORROW_UNAVAILABLE)
                if state.borrow_cost_expires_at_utc is None:
                    _append_unique(reasons, RiskReasonCode.BORROW_COST_MISSING)
                elif state.borrow_cost_expires_at_utc <= context.now_utc:
                    _append_unique(reasons, RiskReasonCode.BORROW_COST_EXPIRED)
        return leg_states

    def _size_order(
        self,
        order: OrderIntent,
        context: RiskEvaluationContext,
        leg_states: dict[str, LegRiskState],
        requested_by_leg: dict[str, Decimal],
        requested_quantity: Decimal,
        limits: dict[str, Decimal],
    ) -> RiskDecision:
        reasons: list[RiskReasonCode] = []
        if requested_quantity <= 0 or order.risk_budget <= 0:
            reason = (
                RiskReasonCode.NO_EXECUTABLE_QUANTITY
                if requested_quantity <= 0
                else RiskReasonCode.RISK_BUDGET_EXHAUSTED
            )
            return self._decision(
                order,
                context,
                RiskDecisionType.BLOCK,
                [reason],
                requested_quantity,
                Decimal("0"),
                limits,
            )

        total_requested_risk = sum(
            (
                requested_by_leg[leg_id] * Decimal(state.stop_distance or 0)
                for leg_id, state in leg_states.items()
            ),
            start=Decimal("0"),
        )
        max_trade_risk = context.account_equity * self.policy.max_risk_per_trade_fraction
        effective_risk_budget = min(Decimal(order.risk_budget), max_trade_risk)
        limits["max_trade_risk"] = max_trade_risk
        limits["order_risk_budget"] = Decimal(order.risk_budget)
        limits["effective_risk_budget"] = effective_risk_budget
        limits["requested_stop_risk"] = total_requested_risk

        scale = Decimal("1")
        if total_requested_risk > max_trade_risk:
            scale = min(scale, max_trade_risk / total_requested_risk)
            _append_unique(reasons, RiskReasonCode.MAX_RISK_PER_TRADE)
        if total_requested_risk > order.risk_budget:
            scale = min(scale, Decimal(order.risk_budget) / total_requested_risk)
            _append_unique(reasons, RiskReasonCode.ORDER_RISK_BUDGET)

        for leg_id, requested in requested_by_leg.items():
            state = leg_states[leg_id]
            available = Decimal(state.available_quantity or 0)
            limits[f"available_quantity:{leg_id}"] = available
            if requested > 0 and available < requested:
                scale = min(scale, available / requested)
                _append_unique(reasons, RiskReasonCode.LIQUIDITY_LIMIT)

        approved_by_leg: dict[str, Decimal] = {}
        for leg_id, requested in requested_by_leg.items():
            unit = leg_states[leg_id].minimum_trade_unit
            scaled = requested * scale
            lots = (scaled / unit).to_integral_value(rounding=ROUND_DOWN)
            approved = lots * unit
            approved_by_leg[leg_id] = approved
            limits[f"approved_quantity:{leg_id}"] = approved
            if approved != scaled:
                _append_unique(reasons, RiskReasonCode.MINIMUM_LOT_ROUNDING)

        approved_quantity = sum(approved_by_leg.values(), start=Decimal("0"))
        approved_risk = sum(
            (
                approved_by_leg[leg_id] * Decimal(leg_states[leg_id].stop_distance or 0)
                for leg_id in approved_by_leg
            ),
            start=Decimal("0"),
        )
        limits["approved_stop_risk"] = approved_risk
        if approved_quantity <= 0 or any(
            requested_by_leg[leg_id] > 0 and approved_by_leg[leg_id] <= 0
            for leg_id in requested_by_leg
        ):
            _append_unique(reasons, RiskReasonCode.NO_EXECUTABLE_QUANTITY)
            return self._decision(
                order,
                context,
                RiskDecisionType.BLOCK,
                reasons,
                requested_quantity,
                Decimal("0"),
                limits,
            )
        if approved_quantity < requested_quantity:
            return self._decision(
                order,
                context,
                RiskDecisionType.REDUCE,
                reasons,
                requested_quantity,
                approved_quantity,
                limits,
            )
        return self._decision(
            order,
            context,
            RiskDecisionType.ALLOW,
            [RiskReasonCode.WITHIN_LIMITS],
            requested_quantity,
            requested_quantity,
            limits,
        )

    def _base_limits(self, context: RiskEvaluationContext) -> dict[str, Decimal]:
        return {
            "max_risk_per_trade_fraction": self.policy.max_risk_per_trade_fraction,
            "max_daily_loss_fraction": self.policy.max_daily_loss_fraction,
            "max_cumulative_mdd_fraction": self.policy.max_cumulative_mdd_fraction,
            "quote_max_age_seconds": self._quote_max_age(context.strategy_class),
            "leg_timeout_seconds": self.policy.leg_timeout_seconds,
        }

    def _quote_max_age(self, strategy_class: StrategyRiskClass) -> Decimal:
        if strategy_class is StrategyRiskClass.H1:
            return self.policy.h1_quote_max_age_seconds
        return self.policy.h2h3_quote_max_age_seconds

    @staticmethod
    def _decision(
        order: OrderIntent,
        context: RiskEvaluationContext,
        decision: RiskDecisionType,
        reasons: list[RiskReasonCode],
        requested_quantity: Decimal,
        approved_quantity: Decimal,
        limits: dict[str, Decimal],
    ) -> RiskDecision:
        return RiskDecision(
            decision_id=f"risk:{order.order_id}:{context.now_utc}",
            order_intent_id=order.order_id,
            decision=decision,
            reason_codes=[reason.value for reason in reasons],
            requested_quantity=requested_quantity,
            approved_quantity=approved_quantity,
            limits_snapshot=limits,
            decided_at_utc=context.now_utc,
        )


def evaluate_order_intent(
    order_intent: OrderIntent,
    context: RiskEvaluationContext,
    policy: RiskPolicy | None = None,
) -> RiskDecision:
    """함수형 호출이 필요한 파이프라인을 위한 편의 진입점."""

    return RiskEngine(policy).evaluate(order_intent, context)
