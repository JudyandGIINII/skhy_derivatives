"""원 15:10 H1 실데이터 가용성 감사와 event-backtest 검증 경로.

저장된 필수 입력이 없으면 합성값으로 채우지 않고 ``HOLD``를 반환한다. 순수 replay
함수는 테스트 배선에도 쓸 수 있지만 원 H1 실데이터 판정은 반드시 먼저 PostgreSQL
가용성 gate를 통과해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, select

from skhy_research.adapters.persistence.schema import normalized_record_catalog
from skhy_research.application.h1_live_snapshot import (
    H1_LIVE_FULL_MODEL_VERSION,
    H1LiveFeatureSet,
)
from skhy_research.domain.enums import (
    OrderSide,
    PromotionVerdict,
    Session,
    SignalDirection,
    TimeInForce,
    Venue,
)
from skhy_research.domain.execution import OrderIntent, OrderLeg, PaperFill
from skhy_research.domain.market import MarketQuote
from skhy_research.domain.reference import FundSnapshot
from skhy_research.domain.simulation_event import SimulationEvent
from skhy_research.engine.backtest import run_backtest
from skhy_research.engine.cost_model import (
    CostComponent,
    CostModelParams,
    estimate_transaction_cost,
    validate_experiment_cost_model,
)
from skhy_research.experiments.promotion import (
    PromotionCriteria,
    PromotionInput,
    PromotionResult,
    evaluate_promotion,
)
from skhy_research.experiments.statistics import (
    TradeResult,
    bootstrap_confidence_interval,
    compute_expectancy,
    compute_max_drawdown,
    compute_profit_factor,
    date_permutation_p_value,
    top_n_day_profit_share,
)
from skhy_research.features.h1_close_pressure.close_pressure import (
    ORIGINAL_H1_LIVE_DATA_RESOLUTION,
    ORIGINAL_H1_PROMOTION_SCOPE,
)
from skhy_research.strategies.h1_close_rebalance.decision_window import (
    H1_ORDER_INTENT_CUTOFF_KST,
    H1_SIGNAL_SNAPSHOT_TIME_KST,
    build_decision_window,
)
from skhy_research.strategies.h1_close_rebalance.strategy import H1CloseRebalanceStrategy


class H1OriginalValidationBlockedError(RuntimeError):
    """원 H1 full input 또는 실행 가능한 replay가 없어 성과 계산을 차단할 때."""


@dataclass(frozen=True)
class H1DataRequirement:
    key: str
    record_type: str
    minimum_records: int
    gate_id: str | None


@dataclass(frozen=True)
class H1OriginalDataAvailability:
    record_counts: tuple[tuple[str, int], ...]
    missing_requirements: tuple[str, ...]
    blocked_gate_ids: tuple[str, ...]
    can_run_real_validation: bool


@dataclass(frozen=True)
class H1OriginalReplayDay:
    trading_date: date
    feature: H1LiveFeatureSet
    fund_snapshots_used: tuple[FundSnapshot, ...]
    entry_quote: MarketQuote
    exit_quote: MarketQuote


@dataclass(frozen=True)
class H1ScenarioMetrics:
    trade_count: int
    cumulative_pnl: Decimal
    expectancy: Decimal
    profit_factor: Decimal
    mdd: Decimal
    mdd_pct: Decimal
    top_1_day_profit_share: Decimal
    top_3_day_profit_share: Decimal
    expectancy_bootstrap_95: tuple[Decimal, Decimal]
    permutation_p_value: Decimal


def _default_h1_cost_components() -> dict[CostComponent, Decimal]:
    return {
        CostComponent.COMMISSION: Decimal("0.00015"),
        CostComponent.TAX: Decimal("0.0018"),
        CostComponent.SPREAD: Decimal("0.0001"),
        CostComponent.SLIPPAGE: Decimal("0.0001"),
        CostComponent.MARKET_IMPACT: Decimal("0.01"),
        CostComponent.PRODUCT_FEES_TRACKING: Decimal("0.0001"),
    }


def _default_promotion_criteria() -> PromotionCriteria:
    return PromotionCriteria(
        min_expectancy=Decimal("0"),
        min_profit_factor=Decimal("1.2"),
        stress_min_cumulative_pnl=Decimal("0"),
        max_single_day_profit_share=Decimal("0.30"),
        max_strategy_mdd_pct=Decimal("5"),
        min_sample_size=30,
    )


@dataclass(frozen=True)
class H1OriginalBacktestConfig:
    initial_capital: Decimal = Decimal("20000000")
    order_quantity: Decimal = Decimal("1")
    neutral_band: Decimal = Decimal("0.001")
    max_participation_rate: Decimal = Decimal("0.1")
    minimum_trading_days: int = 120
    bootstrap_resamples: int = 1000
    permutations: int = 1000
    seed: int = 7
    cost_component_rates: dict[CostComponent, Decimal] = field(
        default_factory=_default_h1_cost_components
    )
    promotion_criteria: PromotionCriteria = field(default_factory=_default_promotion_criteria)

    def __post_init__(self) -> None:
        if self.initial_capital <= 0 or self.order_quantity <= 0:
            raise ValueError("initial_capital과 order_quantity는 양수여야 한다")
        if self.max_participation_rate <= 0 or self.max_participation_rate > 1:
            raise ValueError("max_participation_rate는 0보다 크고 1 이하여야 한다")
        if self.minimum_trading_days < 120:
            raise ValueError("원 H1 minimum_trading_days는 PRD에 따라 120 이상이어야 한다")
        if self.bootstrap_resamples <= 0 or self.permutations <= 0:
            raise ValueError("bootstrap_resamples와 permutations는 양수여야 한다")
        validate_experiment_cost_model("h1_close_rebalance", self.cost_component_rates)


@dataclass(frozen=True)
class H1OriginalBacktestResult:
    base: H1ScenarioMetrics
    base_long: H1ScenarioMetrics
    base_short: H1ScenarioMetrics
    stress_2x: H1ScenarioMetrics
    stress_2x_long: H1ScenarioMetrics
    stress_2x_short: H1ScenarioMetrics
    promotion: PromotionResult
    event_journal_hash: str
    unfilled_round_trips: int


@dataclass(frozen=True)
class H1OriginalStoredValidation:
    availability: H1OriginalDataAvailability
    promotion: PromotionResult
    backtest: H1OriginalBacktestResult | None


_REQUIREMENTS = (
    H1DataRequirement("underlying_120d_bar", "Bar", 120, None),
    H1DataRequirement("h1_1510_market_snapshot", "MarketPriceSnapshot", 120, None),
    H1DataRequirement("prior_nav_aum_replication", "FundSnapshot", 120, None),
    H1DataRequirement("trained_kappa_regime", "H1KappaRegimeEstimate", 1, None),
    H1DataRequirement("close_auction_imbalance", "H1ClosingAuctionImbalance", 120, "G-03"),
    H1DataRequirement("program_net_buy", "H1ProgramNetBuy", 120, "G-03"),
    H1DataRequirement("executable_entry_exit_quote", "MarketQuote", 240, None),
)


def assess_h1_original_data_availability(engine: Engine) -> H1OriginalDataAvailability:
    """현재 normalized catalog가 원 H1 120일 검증에 충분한지 읽기 전용으로 감사한다."""

    with engine.connect() as connection:
        rows = connection.execute(
            select(
                normalized_record_catalog.c.record_type,
                normalized_record_catalog.c.payload,
            )
        ).mappings()
        records = [(str(row["record_type"]), dict(row["payload"])) for row in rows]

    counts: dict[str, int] = {}
    for record_type, payload in records:
        if record_type == "Bar" and payload.get("instrument_id") != "KRX_000660_COMMON_STOCK":
            continue
        if record_type == "FundSnapshot" and (
            payload.get("net_creation_estimate") is None
            or payload.get("replication_type") in (None, "UNKNOWN")
        ):
            continue
        counts[record_type] = counts.get(record_type, 0) + 1

    missing = tuple(
        requirement.key
        for requirement in _REQUIREMENTS
        if counts.get(requirement.record_type, 0) < requirement.minimum_records
    )
    blocked_gates: list[str] = []
    for requirement in _REQUIREMENTS:
        if requirement.key in missing and requirement.gate_id and requirement.gate_id not in blocked_gates:
            blocked_gates.append(requirement.gate_id)
    return H1OriginalDataAvailability(
        record_counts=tuple(sorted(counts.items())),
        missing_requirements=missing,
        blocked_gate_ids=tuple(blocked_gates),
        can_run_real_validation=not missing,
    )


def validate_stored_h1_original(
    engine: Engine,
    *,
    replay_days: list[H1OriginalReplayDay] | None = None,
    config: H1OriginalBacktestConfig | None = None,
) -> H1OriginalStoredValidation:
    """실데이터 gate 통과 전에는 replay를 실행하지 않고 정직한 HOLD를 반환한다."""

    availability = assess_h1_original_data_availability(engine)
    if not availability.can_run_real_validation:
        reasons = (
            "원 H1 실데이터 검증 blocked: "
            f"missing={availability.missing_requirements}, gates={availability.blocked_gate_ids}",
        )
        return H1OriginalStoredValidation(
            availability=availability,
            promotion=PromotionResult(
                verdict=PromotionVerdict.HOLD,
                reasons=reasons,
                model_version=H1_LIVE_FULL_MODEL_VERSION,
                data_resolution=ORIGINAL_H1_LIVE_DATA_RESOLUTION,
                promotion_scope=ORIGINAL_H1_PROMOTION_SCOPE,
                promotion_eligible=False,
            ),
            backtest=None,
        )
    if replay_days is None:
        raise H1OriginalValidationBlockedError(
            "필수 record는 있으나 normalized record를 H1OriginalReplayDay로 매핑한 입력이 없다"
        )
    backtest = run_h1_original_backtest(replay_days, config=config)
    return H1OriginalStoredValidation(availability, backtest.promotion, backtest)


def run_h1_original_backtest(
    replay_days: list[H1OriginalReplayDay],
    *,
    config: H1OriginalBacktestConfig | None = None,
) -> H1OriginalBacktestResult:
    """full 원 H1 feature를 이벤트 엔진·비용·통계·승격판정까지 연결한다."""

    resolved = config or H1OriginalBacktestConfig()
    if not replay_days:
        raise H1OriginalValidationBlockedError("원 H1 replay_days가 비었다")
    ordered_days = sorted(replay_days, key=lambda item: item.trading_date)
    if len({item.trading_date for item in ordered_days}) != len(ordered_days):
        raise ValueError("원 H1 replay trading_date가 중복됐다")
    for item in ordered_days:
        _validate_replay_day(item)

    strategy = H1CloseRebalanceStrategy(
        strategy_version=H1_LIVE_FULL_MODEL_VERSION,
        neutral_band=resolved.neutral_band,
    )
    events: list[SimulationEvent] = []
    orders: list[OrderIntent] = []
    active_days: list[tuple[H1OriginalReplayDay, SignalDirection]] = []
    for item in ordered_days:
        window = build_decision_window(
            item.trading_date,
            H1_SIGNAL_SNAPSHOT_TIME_KST,
            H1_ORDER_INTENT_CUTOFF_KST,
        )
        decision = strategy.decide(
            instrument_id=item.feature.underlying_instrument_id,
            feature_set_id=f"{item.feature.model_version}:{item.trading_date.isoformat()}",
            close_pressure=item.feature.close_pressure,
            input_record_ids=list(item.feature.input_record_ids),
            fund_snapshots_used=list(item.fund_snapshots_used),
            decision_time_utc=item.feature.decision_time_utc,
            expires_at_utc=window.order_intent_cutoff_utc,
            signal_id=f"h1-signal:{item.trading_date.isoformat()}",
            estimated_cost=sum(resolved.cost_component_rates.values(), start=Decimal("0")),
            live_snapshots_used=list(item.feature.live_snapshots_used),
        )
        if decision.signal is None:
            continue
        direction = decision.signal.direction
        active_days.append((item, direction))
        day_events, day_orders = _build_day_replay(item, direction, resolved.order_quantity)
        events.extend(day_events)
        orders.extend(day_orders)

    engine_result = run_backtest(
        events,
        orders,
        max_participation_rate=resolved.max_participation_rate,
        seed=resolved.seed,
    )
    fills_by_order: dict[str, list[PaperFill]] = {}
    for fill in engine_result.fills:
        fills_by_order.setdefault(fill.order_id, []).append(fill)

    params = CostModelParams(
        commission_rate=resolved.cost_component_rates[CostComponent.COMMISSION],
        tax_rate=resolved.cost_component_rates[CostComponent.TAX],
        market_impact_coefficient=resolved.cost_component_rates[CostComponent.MARKET_IMPACT],
    )
    base_trades: list[TradeResult] = []
    stress_trades: list[TradeResult] = []
    base_by_direction: dict[SignalDirection, list[TradeResult]] = {
        SignalDirection.LONG: [],
        SignalDirection.SHORT: [],
    }
    stress_by_direction: dict[SignalDirection, list[TradeResult]] = {
        SignalDirection.LONG: [],
        SignalDirection.SHORT: [],
    }
    unfilled = 0
    for item, direction in active_days:
        prefix = item.trading_date.isoformat()
        entry = _aggregate_fill(fills_by_order.get(f"h1-entry:{prefix}", []), resolved.order_quantity)
        exit_fill = _aggregate_fill(
            fills_by_order.get(f"h1-exit:{prefix}", []), resolved.order_quantity
        )
        if entry is None or exit_fill is None:
            unfilled += 1
            continue
        gross = (
            (exit_fill - entry) * resolved.order_quantity
            if direction is SignalDirection.LONG
            else (entry - exit_fill) * resolved.order_quantity
        )
        entry_is_sell = direction is SignalDirection.SHORT
        exit_is_sell = not entry_is_sell
        base_cost = _round_trip_cost(
            item,
            resolved.order_quantity,
            params,
            resolved.cost_component_rates,
            entry_is_sell=entry_is_sell,
            exit_is_sell=exit_is_sell,
        )
        base_trade = TradeResult(f"h1:{prefix}", gross - base_cost, item.trading_date)
        stress_trade = TradeResult(
            f"h1:{prefix}:stress", gross - base_cost * 2, item.trading_date
        )
        base_trades.append(base_trade)
        stress_trades.append(stress_trade)
        base_by_direction[direction].append(base_trade)
        stress_by_direction[direction].append(stress_trade)

    base_metrics = _scenario_metrics(base_trades, resolved, seed_offset=0)
    stress_metrics = _scenario_metrics(stress_trades, resolved, seed_offset=10_000)
    base_long = _scenario_metrics(
        base_by_direction[SignalDirection.LONG], resolved, seed_offset=1_000
    )
    base_short = _scenario_metrics(
        base_by_direction[SignalDirection.SHORT], resolved, seed_offset=2_000
    )
    stress_long = _scenario_metrics(
        stress_by_direction[SignalDirection.LONG], resolved, seed_offset=11_000
    )
    stress_short = _scenario_metrics(
        stress_by_direction[SignalDirection.SHORT], resolved, seed_offset=12_000
    )
    promotion = evaluate_promotion(
        PromotionInput(
            trade_count=base_metrics.trade_count,
            expectancy=base_metrics.expectancy,
            profit_factor=base_metrics.profit_factor,
            stress_cumulative_pnl=stress_metrics.cumulative_pnl,
            top_1_day_profit_share=base_metrics.top_1_day_profit_share,
            mdd_pct=base_metrics.mdd_pct,
            model_version=H1_LIVE_FULL_MODEL_VERSION,
            data_resolution=ORIGINAL_H1_LIVE_DATA_RESOLUTION,
            promotion_scope=ORIGINAL_H1_PROMOTION_SCOPE,
            promotion_eligible=True,
        ),
        resolved.promotion_criteria,
    )
    if len(ordered_days) < resolved.minimum_trading_days:
        promotion = PromotionResult(
            PromotionVerdict.HOLD,
            (
                f"H1 거래일 부족: {len(ordered_days)} < {resolved.minimum_trading_days}",
            ),
            H1_LIVE_FULL_MODEL_VERSION,
            ORIGINAL_H1_LIVE_DATA_RESOLUTION,
            ORIGINAL_H1_PROMOTION_SCOPE,
            True,
        )
    elif unfilled:
        promotion = PromotionResult(
            PromotionVerdict.REJECT,
            (f"미체결 round trip={unfilled}",),
            H1_LIVE_FULL_MODEL_VERSION,
            ORIGINAL_H1_LIVE_DATA_RESOLUTION,
            ORIGINAL_H1_PROMOTION_SCOPE,
            True,
        )
    return H1OriginalBacktestResult(
        base=base_metrics,
        base_long=base_long,
        base_short=base_short,
        stress_2x=stress_metrics,
        stress_2x_long=stress_long,
        stress_2x_short=stress_short,
        promotion=promotion,
        event_journal_hash=engine_result.event_journal_hash,
        unfilled_round_trips=unfilled,
    )


def _validate_replay_day(item: H1OriginalReplayDay) -> None:
    feature = item.feature
    if feature.trading_date != item.trading_date:
        raise ValueError("feature trading_date와 replay trading_date가 다르다")
    if feature.model_version != H1_LIVE_FULL_MODEL_VERSION:
        raise H1OriginalValidationBlockedError(
            f"full 원 H1이 아닌 model={feature.model_version}"
        )
    if not feature.promotion_eligible or not feature.close_pressure.promotion_eligible:
        raise H1OriginalValidationBlockedError(
            "G-03 flow 결측 feature는 원 H1 backtest에 넣을 수 없다"
        )
    if feature.close_pressure.missing_flow_fund_ids:
        raise H1OriginalValidationBlockedError("missing observable flow가 있는 원 H1 feature")
    if not item.fund_snapshots_used:
        raise H1OriginalValidationBlockedError("원 H1 replay에 prior FundSnapshot lineage가 없다")
    window = build_decision_window(
        item.trading_date,
        H1_SIGNAL_SNAPSHOT_TIME_KST,
        H1_ORDER_INTENT_CUTOFF_KST,
    )
    if not (feature.decision_time_utc < item.entry_quote.event_time_utc <= window.order_intent_cutoff_utc):
        raise ValueError("entry quote가 decision 이후~15:19:30 cutoff 범위가 아니다")
    if item.entry_quote.received_time_utc > window.order_intent_cutoff_utc:
        raise ValueError("entry quote가 15:19:30 cutoff 후에 가용해졌다")
    if item.exit_quote.event_time_utc <= window.order_intent_cutoff_utc:
        raise ValueError("exit quote는 15:19:30 cutoff 이후여야 한다")
    for quote in (item.entry_quote, item.exit_quote):
        if quote.instrument_id != feature.underlying_instrument_id:
            raise ValueError("replay quote instrument가 H1 기초자산과 다르다")
        if quote.venue is not Venue.KRX:
            raise ValueError("현재 원 H1 replay는 KRX 체결 호가만 지원한다")
        if quote.received_time_utc < quote.event_time_utc:
            raise ValueError("replay quote received time이 event time보다 이르다")
    if item.exit_quote.session is not Session.CLOSE_AUCTION:
        raise ValueError("현재 원 H1 KRX replay exit은 종가 경매 호가여야 한다")


def _build_day_replay(
    item: H1OriginalReplayDay,
    direction: SignalDirection,
    quantity: Decimal,
) -> tuple[list[SimulationEvent], list[OrderIntent]]:
    window = build_decision_window(
        item.trading_date,
        H1_SIGNAL_SNAPSHOT_TIME_KST,
        H1_ORDER_INTENT_CUTOFF_KST,
    )
    entry_side = OrderSide.BUY if direction is SignalDirection.LONG else OrderSide.SELL
    exit_side = OrderSide.SELL if entry_side is OrderSide.BUY else OrderSide.BUY
    prefix = item.trading_date.isoformat()
    entry_limit = (
        item.entry_quote.ask_price if entry_side is OrderSide.BUY else item.entry_quote.bid_price
    )
    exit_limit = (
        item.exit_quote.ask_price if exit_side is OrderSide.BUY else item.exit_quote.bid_price
    )
    entry = _order(
        f"h1-entry:{prefix}",
        item.feature,
        entry_side,
        entry_limit,
        quantity,
        item.feature.decision_time_utc,
        window.order_intent_cutoff_utc,
    )
    exit_order = _order(
        f"h1-exit:{prefix}",
        item.feature,
        exit_side,
        exit_limit,
        quantity,
        window.order_intent_cutoff_utc,
        item.exit_quote.event_time_utc,
    )
    events = [
        SimulationEvent(
            event_id=f"h1-entry-quote:{prefix}",
            available_time_utc=item.entry_quote.received_time_utc,
            event_time_utc=item.entry_quote.event_time_utc,
            venue=item.entry_quote.venue.value,
            event_type="quote",
            provider_sequence=None,
            payload=item.entry_quote,
        ),
        SimulationEvent(
            event_id=f"h1-exit-quote:{prefix}",
            available_time_utc=item.exit_quote.received_time_utc,
            event_time_utc=item.exit_quote.event_time_utc,
            venue=item.exit_quote.venue.value,
            event_type="quote",
            provider_sequence=None,
            payload=item.exit_quote,
        ),
    ]
    return events, [entry, exit_order]


def _order(
    order_id: str,
    feature: H1LiveFeatureSet,
    side: OrderSide,
    limit_price: Decimal,
    quantity: Decimal,
    created_at_utc: int,
    expires_at_utc: int,
) -> OrderIntent:
    return OrderIntent(
        order_id=order_id,
        signal_id=order_id.replace("entry", "signal").replace("exit", "signal"),
        strategy_id="h1_close_rebalance",
        legs=[
            OrderLeg(
                leg_id=f"leg:{order_id}",
                instrument_id=feature.underlying_instrument_id,
                venue=Venue.KRX,
                side=side,
                quantity=quantity,
                limit_price=limit_price,
                time_in_force=TimeInForce.DAY,
            )
        ],
        risk_budget=limit_price * quantity,
        created_at_utc=created_at_utc,
        expires_at_utc=expires_at_utc,
        idempotency_key=f"idem:{order_id}",
    )


def _aggregate_fill(fills: list[PaperFill], requested_quantity: Decimal) -> Decimal | None:
    quantity = sum((item.filled_quantity for item in fills), start=Decimal("0"))
    if quantity != requested_quantity:
        return None
    notional = sum(
        (item.fill_price * item.filled_quantity for item in fills),
        start=Decimal("0"),
    )
    return notional / quantity


def _round_trip_cost(
    item: H1OriginalReplayDay,
    quantity: Decimal,
    params: CostModelParams,
    components: dict[CostComponent, Decimal],
    *,
    entry_is_sell: bool,
    exit_is_sell: bool,
) -> Decimal:
    entry = estimate_transaction_cost(
        item.entry_quote.bid_price,
        item.entry_quote.ask_price,
        quantity,
        max(item.entry_quote.bid_size, item.entry_quote.ask_size),
        params,
        entry_is_sell,
    )
    exit_cost = estimate_transaction_cost(
        item.exit_quote.bid_price,
        item.exit_quote.ask_price,
        quantity,
        max(item.exit_quote.bid_size, item.exit_quote.ask_size),
        params,
        exit_is_sell,
    )
    entry_mid = (item.entry_quote.bid_price + item.entry_quote.ask_price) / 2
    exit_mid = (item.exit_quote.bid_price + item.exit_quote.ask_price) / 2
    extra_rate = components[CostComponent.SLIPPAGE] + components[
        CostComponent.PRODUCT_FEES_TRACKING
    ]
    additional = (entry_mid + exit_mid) * quantity * extra_rate
    return entry.total + exit_cost.total + additional


def _scenario_metrics(
    trades: list[TradeResult], config: H1OriginalBacktestConfig, *, seed_offset: int
) -> H1ScenarioMetrics:
    ordered = sorted(trades, key=lambda item: (item.trading_date, item.trade_id))
    daily_pnl: dict[date, Decimal] = {}
    for trade in ordered:
        daily_pnl[trade.trading_date] = daily_pnl.get(trade.trading_date, Decimal("0")) + trade.pnl
    cumulative = sum((item.pnl for item in ordered), start=Decimal("0"))
    mdd = compute_max_drawdown(ordered)
    bootstrap = bootstrap_confidence_interval(
        [item.pnl for item in ordered],
        n_resamples=config.bootstrap_resamples,
        confidence=0.95,
        seed=config.seed + seed_offset,
    )
    permutation = date_permutation_p_value(
        daily_pnl,
        n_permutations=config.permutations,
        seed=config.seed + seed_offset,
    )
    return H1ScenarioMetrics(
        trade_count=len(ordered),
        cumulative_pnl=cumulative,
        expectancy=compute_expectancy(ordered),
        profit_factor=compute_profit_factor(ordered),
        mdd=mdd,
        mdd_pct=mdd / config.initial_capital * Decimal("100"),
        top_1_day_profit_share=top_n_day_profit_share(daily_pnl, 1),
        top_3_day_profit_share=top_n_day_profit_share(daily_pnl, 3),
        expectancy_bootstrap_95=bootstrap,
        permutation_p_value=Decimal(str(permutation)),
    )
