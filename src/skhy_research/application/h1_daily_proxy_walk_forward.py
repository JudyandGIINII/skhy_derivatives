"""실 KRX daily-proxy feature의 결정론적 walk-forward 연구 실행기."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select

from skhy_research.adapters.persistence.schema import normalized_record_catalog
from skhy_research.application.config import Settings
from skhy_research.application.h1_krx_daily_proxy import (
    KRX_DAILY_PROXY_DATA_RESOLUTION,
    KRX_DAILY_PROXY_MODEL_VERSION,
    KRX_DAILY_PROXY_PROMOTION_SCOPE,
    KrxDailyProxyFundInput,
    KrxDailyProxyMarketInput,
    build_krx_daily_proxy_feature,
)
from skhy_research.domain.calendar import utc_nanos_to_local_datetime
from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    OrderSide,
    Session,
    SignalDirection,
    TimeInForce,
    Venue,
)
from skhy_research.domain.execution import OrderIntent, OrderLeg
from skhy_research.domain.krx_etp import KrxEtpDailySnapshot
from skhy_research.domain.market import Bar, MarketQuote
from skhy_research.domain.simulation_event import SimulationEvent
from skhy_research.engine.backtest import run_backtest
from skhy_research.engine.cost_model import CostModelParams, estimate_transaction_cost
from skhy_research.experiments.promotion import (
    PromotionCriteria,
    PromotionInput,
    PromotionResult,
    evaluate_promotion,
)
from skhy_research.experiments.splits import (
    TimeSplit,
    chronological_split,
    walk_forward_splits,
)
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
from skhy_research.strategies.h1_close_rebalance.strategy import H1CloseRebalanceStrategy


@dataclass(frozen=True)
class StoredKrxDailyBar:
    normalized_record_id: str
    created_at_utc: int
    bar: Bar

    @property
    def trading_date(self) -> date:
        return utc_nanos_to_local_datetime(self.bar.bar_close_time_utc, Venue.KRX).date()


@dataclass(frozen=True)
class StoredKrxEtpSnapshot:
    normalized_record_id: str
    created_at_utc: int
    snapshot: KrxEtpDailySnapshot


@dataclass(frozen=True)
class DailyProxyBacktestConfig:
    seed: int = 7
    trading_days: int = 120
    kappa: Decimal = Decimal("0.10")
    neutral_band: Decimal = Decimal("0.001")
    order_quantity: Decimal = Decimal("1")
    initial_capital: Decimal = Decimal("10000000")
    commission_rate: Decimal = Decimal("0.00015")
    tax_rate: Decimal = Decimal("0.0018")
    market_impact_coefficient: Decimal = Decimal("0.1")
    bootstrap_resamples: int = 1000
    permutation_count: int = 1000
    confidence: float = 0.95
    min_sample_size: int = 30

    def __post_init__(self) -> None:
        if self.trading_days <= 0:
            raise ValueError("trading_days는 양수여야 한다")
        for name, value in (
            ("kappa", self.kappa),
            ("neutral_band", self.neutral_band),
            ("order_quantity", self.order_quantity),
            ("initial_capital", self.initial_capital),
            ("commission_rate", self.commission_rate),
            ("tax_rate", self.tax_rate),
            ("market_impact_coefficient", self.market_impact_coefficient),
        ):
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name}은 0 이상의 유한 Decimal이어야 한다")
        if self.order_quantity == 0 or self.initial_capital == 0:
            raise ValueError("order_quantity와 initial_capital은 0보다 커야 한다")
        if self.bootstrap_resamples <= 0 or self.permutation_count <= 0:
            raise ValueError("bootstrap_resamples와 permutation_count는 양수여야 한다")
        if not 0 < self.confidence < 1:
            raise ValueError("confidence는 0과 1 사이여야 한다")
        if self.min_sample_size <= 0:
            raise ValueError("min_sample_size는 양수여야 한다")


@dataclass(frozen=True)
class ScenarioStatistics:
    trade_count: int
    cumulative_pnl: Decimal
    expectancy: Decimal
    median_trade_pnl: Decimal
    win_rate: Decimal
    profit_factor: Decimal
    max_drawdown: Decimal
    mdd_pct: Decimal
    top_1_day_profit_share: Decimal
    bootstrap_expectancy_ci: tuple[Decimal, Decimal]
    permutation_p_value: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "trade_count": self.trade_count,
            "cumulative_pnl": str(self.cumulative_pnl),
            "expectancy": str(self.expectancy),
            "median_trade_pnl": str(self.median_trade_pnl),
            "win_rate": str(self.win_rate),
            "profit_factor": str(self.profit_factor),
            "max_drawdown": str(self.max_drawdown),
            "mdd_pct": str(self.mdd_pct),
            "top_1_day_profit_share": str(self.top_1_day_profit_share),
            "bootstrap_expectancy_ci": [
                str(self.bootstrap_expectancy_ci[0]),
                str(self.bootstrap_expectancy_ci[1]),
            ],
            "permutation_p_value": str(self.permutation_p_value),
        }


@dataclass(frozen=True)
class WalkForwardFoldResult:
    fold_number: int
    train: TimeSplit
    test: TimeSplit
    available_feature_count: int
    neutral_feature_count: int
    engine_fill_count: int
    event_journal_hash: str
    base: ScenarioStatistics
    stress_2x: ScenarioStatistics

    def to_dict(self) -> dict[str, object]:
        return {
            "fold_number": self.fold_number,
            "train": _split_dict(self.train),
            "test": _split_dict(self.test),
            "available_feature_count": self.available_feature_count,
            "neutral_feature_count": self.neutral_feature_count,
            "engine_fill_count": self.engine_fill_count,
            "event_journal_hash": self.event_journal_hash,
            "base": self.base.to_dict(),
            "stress_2x": self.stress_2x.to_dict(),
        }


@dataclass(frozen=True)
class DailyProxyWalkForwardResult:
    data_snapshot_hash: str
    result_hash: str
    bar_count: int
    etp_snapshot_count: int
    available_feature_count: int
    chronological_splits: tuple[TimeSplit, ...]
    folds: tuple[WalkForwardFoldResult, ...]
    aggregate_base: ScenarioStatistics
    aggregate_stress_2x: ScenarioStatistics
    promotion: PromotionResult
    config: DailyProxyBacktestConfig

    def to_dict(self) -> dict[str, object]:
        payload = _result_payload(
            data_snapshot_hash=self.data_snapshot_hash,
            bar_count=self.bar_count,
            etp_snapshot_count=self.etp_snapshot_count,
            available_feature_count=self.available_feature_count,
            chronological_splits=self.chronological_splits,
            folds=self.folds,
            aggregate_base=self.aggregate_base,
            aggregate_stress_2x=self.aggregate_stress_2x,
            promotion=self.promotion,
            config=self.config,
        )
        return {**payload, "result_hash": self.result_hash}


@dataclass(frozen=True)
class _TradeCandidate:
    basis_date: date
    signal_date: date
    direction: SignalDirection
    target_bar: Bar
    replay_open_time_utc: int
    close_pressure: Decimal


@dataclass(frozen=True)
class _ExecutedFold:
    result: WalkForwardFoldResult
    base_trades: tuple[TradeResult, ...]
    stress_trades: tuple[TradeResult, ...]


def load_latest_krx_daily_bars(
    engine: Engine,
    *,
    instrument_id: str = "KRX_000660_COMMON_STOCK",
    trading_days: int = 120,
) -> tuple[StoredKrxDailyBar, ...]:
    """normalized catalog에서 지정 종목의 최신 N개 실제 1d Bar를 읽는다."""

    with engine.connect() as conn:
        rows = conn.execute(
            select(
                normalized_record_catalog.c.normalized_record_id,
                normalized_record_catalog.c.payload,
                normalized_record_catalog.c.created_at_utc,
            ).where(normalized_record_catalog.c.record_type == "Bar")
        ).mappings().all()
    stored = [
        StoredKrxDailyBar(
            normalized_record_id=str(row["normalized_record_id"]),
            created_at_utc=int(row["created_at_utc"]),
            bar=Bar.model_validate(row["payload"]),
        )
        for row in rows
        if row["payload"].get("instrument_id") == instrument_id
        and row["payload"].get("period") == "1d"
    ]
    stored.sort(key=lambda item: item.trading_date)
    if len(stored) < trading_days:
        raise ValueError(
            f"{instrument_id} KRX Bar 부족: required={trading_days}, actual={len(stored)}"
        )
    selected = tuple(stored[-trading_days:])
    dates = [item.trading_date for item in selected]
    if len(set(dates)) != len(dates):
        raise ValueError(f"{instrument_id} KRX Bar 거래일이 중복됨")
    return selected


def load_krx_etp_daily_snapshots(
    engine: Engine,
    *,
    target_underlying: str = "SK하이닉스",
) -> tuple[StoredKrxEtpSnapshot, ...]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                normalized_record_catalog.c.normalized_record_id,
                normalized_record_catalog.c.payload,
                normalized_record_catalog.c.created_at_utc,
            ).where(normalized_record_catalog.c.record_type == "KrxEtpDailySnapshot")
        ).mappings().all()
    stored = [
        StoredKrxEtpSnapshot(
            normalized_record_id=str(row["normalized_record_id"]),
            created_at_utc=int(row["created_at_utc"]),
            snapshot=KrxEtpDailySnapshot.model_validate(row["payload"]),
        )
        for row in rows
        if row["payload"].get("underlying_name") == target_underlying
    ]
    stored.sort(key=lambda item: (item.snapshot.basis_date, item.snapshot.source_symbol))
    return tuple(stored)


def run_h1_daily_proxy_walk_forward(
    engine: Engine,
    settings: Settings,
    config: DailyProxyBacktestConfig | None = None,
) -> DailyProxyWalkForwardResult:
    """실 KRX catalog를 읽어 feature→engine→통계→HOLD 판정을 재현한다."""

    config = config or DailyProxyBacktestConfig()
    required_days = (
        settings.h1.split_train_days
        + settings.h1.split_validation_days
        + settings.h1.split_test_days
    )
    if config.trading_days < required_days:
        raise ValueError(
            f"chronological split에 거래일이 부족함: required={required_days}, "
            f"configured={config.trading_days}"
        )
    bars = load_latest_krx_daily_bars(engine, trading_days=config.trading_days)
    etp = load_krx_etp_daily_snapshots(engine)
    selected_dates = {item.trading_date for item in bars}
    etp = tuple(item for item in etp if item.snapshot.basis_date in selected_dates)
    if not etp:
        raise ValueError("선택한 KRX Bar 기간에 SK하이닉스 ETP daily snapshot이 없음")

    trading_dates = [item.trading_date for item in bars]
    chronological = tuple(
        chronological_split(
            trading_dates,
            settings.h1.split_train_days,
            settings.h1.split_validation_days,
            settings.h1.split_test_days,
        )
    )
    walk_forward = walk_forward_splits(
        trading_dates,
        initial_train_days=settings.h1.split_train_days,
        step_days=settings.h1.split_validation_days,
        test_days=settings.h1.split_test_days,
    )
    if len(walk_forward) % 2 != 0:
        raise AssertionError("walk-forward split은 train/test 쌍이어야 한다")

    candidates, feature_dates = _build_candidates(bars, etp, config)
    available_feature_count = len(feature_dates)
    cost_params = CostModelParams(
        commission_rate=config.commission_rate,
        tax_rate=config.tax_rate,
        market_impact_coefficient=config.market_impact_coefficient,
    )
    stress_multiplier = Decimal(str(settings.cost_stress_multiplier))
    executed: list[_ExecutedFold] = []
    for pair_index in range(0, len(walk_forward), 2):
        train = walk_forward[pair_index]
        test = walk_forward[pair_index + 1]
        fold_number = pair_index // 2 + 1
        fold_candidates = [
            item for item in candidates if test.start <= item.signal_date <= test.end
        ]
        fold_feature_count = sum(test.start <= item <= test.end for item in feature_dates)
        executed.append(
            _execute_fold(
                fold_number=fold_number,
                train=train,
                test=test,
                candidates=fold_candidates,
                available_feature_count=fold_feature_count,
                cost_params=cost_params,
                stress_multiplier=stress_multiplier,
                config=config,
            )
        )

    base_trades = tuple(trade for item in executed for trade in item.base_trades)
    stress_trades = tuple(trade for item in executed for trade in item.stress_trades)
    aggregate_base = _statistics(list(base_trades), config=config, seed=config.seed + 10_000)
    aggregate_stress = _statistics(
        list(stress_trades), config=config, seed=config.seed + 20_000
    )
    promotion = evaluate_promotion(
        PromotionInput(
            trade_count=aggregate_base.trade_count,
            expectancy=aggregate_base.expectancy,
            profit_factor=aggregate_base.profit_factor,
            stress_cumulative_pnl=aggregate_stress.cumulative_pnl,
            top_1_day_profit_share=aggregate_base.top_1_day_profit_share,
            mdd_pct=aggregate_base.mdd_pct,
            model_version=KRX_DAILY_PROXY_MODEL_VERSION,
            data_resolution=KRX_DAILY_PROXY_DATA_RESOLUTION,
            promotion_scope=KRX_DAILY_PROXY_PROMOTION_SCOPE,
            promotion_eligible=False,
        ),
        PromotionCriteria(
            min_expectancy=Decimal(str(settings.promotion.min_expectancy)),
            min_profit_factor=Decimal(str(settings.promotion.min_profit_factor)),
            stress_min_cumulative_pnl=Decimal(
                str(settings.promotion.stress_min_cumulative_pnl)
            ),
            max_single_day_profit_share=Decimal(
                str(settings.promotion.max_single_day_profit_share)
            ),
            max_strategy_mdd_pct=Decimal(str(settings.promotion.max_strategy_mdd_pct)),
            min_sample_size=config.min_sample_size,
        ),
    )
    snapshot_hash = _data_snapshot_hash(bars, etp)
    folds = tuple(item.result for item in executed)
    payload = _result_payload(
        data_snapshot_hash=snapshot_hash,
        bar_count=len(bars),
        etp_snapshot_count=len(etp),
        available_feature_count=available_feature_count,
        chronological_splits=chronological,
        folds=folds,
        aggregate_base=aggregate_base,
        aggregate_stress_2x=aggregate_stress,
        promotion=promotion,
        config=config,
    )
    return DailyProxyWalkForwardResult(
        data_snapshot_hash=snapshot_hash,
        result_hash=_canonical_hash(payload),
        bar_count=len(bars),
        etp_snapshot_count=len(etp),
        available_feature_count=available_feature_count,
        chronological_splits=chronological,
        folds=folds,
        aggregate_base=aggregate_base,
        aggregate_stress_2x=aggregate_stress,
        promotion=promotion,
        config=config,
    )


def _build_candidates(
    bars: tuple[StoredKrxDailyBar, ...],
    etp: tuple[StoredKrxEtpSnapshot, ...],
    config: DailyProxyBacktestConfig,
) -> tuple[list[_TradeCandidate], list[date]]:
    etp_by_date: dict[date, list[StoredKrxEtpSnapshot]] = {}
    for item in etp:
        etp_by_date.setdefault(item.snapshot.basis_date, []).append(item)

    max_received_at = max(
        [item.created_at_utc for item in bars] + [item.created_at_utc for item in etp]
    )
    replay_base = max_received_at + 1_000_000
    strategy = H1CloseRebalanceStrategy(
        strategy_version=KRX_DAILY_PROXY_MODEL_VERSION,
        neutral_band=config.neutral_band,
        promotion_scope=KRX_DAILY_PROXY_PROMOTION_SCOPE,
    )
    candidates: list[_TradeCandidate] = []
    feature_dates: list[date] = []
    for index in range(19, len(bars) - 1):
        basis = bars[index]
        target = bars[index + 1]
        fund_snapshots = etp_by_date.get(basis.trading_date, [])
        if not fund_snapshots:
            continue
        replay_open_time = replay_base + (index + 1) * 100
        as_of_time = replay_open_time - 2
        market_window = bars[index - 19 : index + 1]
        market_input = KrxDailyProxyMarketInput(
            basis_date=basis.trading_date,
            previous_close=bars[index - 1].bar.close,
            close=basis.bar.close,
            turnover_notional_20d=tuple(
                _required_turnover(item.bar) for item in market_window
            ),
            received_at_utc=max(item.created_at_utc for item in market_window),
            input_record_ids=tuple(item.normalized_record_id for item in market_window),
        )
        fund_inputs = [
            KrxDailyProxyFundInput(
                fund_id=item.snapshot.fund_id,
                beta=item.snapshot.leverage_factor,
                nav_or_iv=item.snapshot.nav_or_indicative_value,
                listed_shares=item.snapshot.listed_shares,
                kappa=config.kappa,
                basis_date=item.snapshot.basis_date,
                received_at_utc=item.created_at_utc,
                input_record_ids=(item.normalized_record_id,),
            )
            for item in fund_snapshots
        ]
        feature = build_krx_daily_proxy_feature(
            fund_inputs,
            market_input,
            signal_date=target.trading_date,
            as_of_time_utc=as_of_time,
        )
        feature_dates.append(target.trading_date)
        decision = strategy.decide(
            instrument_id=basis.bar.instrument_id,
            feature_set_id=f"{KRX_DAILY_PROXY_MODEL_VERSION}:{basis.trading_date.isoformat()}",
            close_pressure=feature.close_pressure,
            input_record_ids=list(feature.input_record_ids),
            fund_snapshots_used=[],
            decision_time_utc=as_of_time,
            expires_at_utc=replay_open_time + 4,
            signal_id=f"daily-proxy:{target.trading_date.isoformat()}",
            estimated_cost=config.commission_rate * 2 + config.tax_rate,
        )
        if decision.signal is None:
            continue
        candidates.append(
            _TradeCandidate(
                basis_date=basis.trading_date,
                signal_date=target.trading_date,
                direction=decision.signal.direction,
                target_bar=target.bar,
                replay_open_time_utc=replay_open_time,
                close_pressure=feature.close_pressure.value,
            )
        )
    return candidates, feature_dates


def _execute_fold(
    *,
    fold_number: int,
    train: TimeSplit,
    test: TimeSplit,
    candidates: list[_TradeCandidate],
    available_feature_count: int,
    cost_params: CostModelParams,
    stress_multiplier: Decimal,
    config: DailyProxyBacktestConfig,
) -> _ExecutedFold:
    events: list[SimulationEvent] = []
    orders: list[OrderIntent] = []
    for candidate in candidates:
        trade_events, trade_orders = _build_trade_replay(candidate, config.order_quantity)
        events.extend(trade_events)
        orders.extend(trade_orders)
    engine_result = run_backtest(
        events,
        orders,
        max_participation_rate=Decimal("1"),
        seed=config.seed,
    )
    fills_by_order = {fill.order_id: fill for fill in engine_result.fills}
    base_trades: list[TradeResult] = []
    stress_trades: list[TradeResult] = []
    for candidate in candidates:
        prefix = candidate.signal_date.isoformat()
        entry = fills_by_order.get(f"daily-proxy-entry:{prefix}")
        exit_fill = fills_by_order.get(f"daily-proxy-exit:{prefix}")
        if entry is None or exit_fill is None:
            raise RuntimeError(f"daily-proxy replay 체결 누락: {prefix}")
        gross_pnl = (
            (exit_fill.fill_price - entry.fill_price) * config.order_quantity
            if candidate.direction is SignalDirection.LONG
            else (entry.fill_price - exit_fill.fill_price) * config.order_quantity
        )
        entry_is_sell = candidate.direction is SignalDirection.SHORT
        exit_is_sell = candidate.direction is SignalDirection.LONG
        depth = max(candidate.target_bar.volume, config.order_quantity)
        entry_cost = estimate_transaction_cost(
            entry.fill_price,
            entry.fill_price,
            config.order_quantity,
            depth,
            cost_params,
            entry_is_sell,
        )
        exit_cost = estimate_transaction_cost(
            exit_fill.fill_price,
            exit_fill.fill_price,
            config.order_quantity,
            depth,
            cost_params,
            exit_is_sell,
        )
        total_cost = entry_cost.total + exit_cost.total
        stressed_cost = (
            entry_cost.stressed(stress_multiplier).total
            + exit_cost.stressed(stress_multiplier).total
        )
        base_trades.append(
            TradeResult(
                trade_id=f"daily-proxy:{prefix}",
                pnl=gross_pnl - total_cost,
                trading_date=candidate.signal_date,
            )
        )
        stress_trades.append(
            TradeResult(
                trade_id=f"daily-proxy:{prefix}:stress",
                pnl=gross_pnl - stressed_cost,
                trading_date=candidate.signal_date,
            )
        )

    base_stats = _statistics(base_trades, config=config, seed=config.seed + fold_number)
    stress_stats = _statistics(
        stress_trades, config=config, seed=config.seed + 1000 + fold_number
    )
    return _ExecutedFold(
        result=WalkForwardFoldResult(
            fold_number=fold_number,
            train=train,
            test=test,
            available_feature_count=available_feature_count,
            neutral_feature_count=available_feature_count - len(candidates),
            engine_fill_count=len(engine_result.fills),
            event_journal_hash=engine_result.event_journal_hash,
            base=base_stats,
            stress_2x=stress_stats,
        ),
        base_trades=tuple(base_trades),
        stress_trades=tuple(stress_trades),
    )


def _build_trade_replay(
    candidate: _TradeCandidate, quantity: Decimal
) -> tuple[list[SimulationEvent], list[OrderIntent]]:
    open_time = candidate.replay_open_time_utc
    close_time = open_time + 3
    prefix = candidate.signal_date.isoformat()
    if candidate.direction is SignalDirection.LONG:
        entry_side, exit_side = OrderSide.BUY, OrderSide.SELL
    else:
        entry_side, exit_side = OrderSide.SELL, OrderSide.BUY

    entry_order = _order(
        order_id=f"daily-proxy-entry:{prefix}",
        side=entry_side,
        price=candidate.target_bar.open,
        quantity=quantity,
        created_at_utc=open_time - 1,
        expires_at_utc=open_time + 1,
    )
    exit_order = _order(
        order_id=f"daily-proxy-exit:{prefix}",
        side=exit_side,
        price=candidate.target_bar.close,
        quantity=quantity,
        created_at_utc=open_time + 1,
        expires_at_utc=close_time + 1,
    )
    open_quote = _quote(candidate.target_bar, candidate.target_bar.open, open_time)
    close_quote = _quote(candidate.target_bar, candidate.target_bar.close, close_time)
    return (
        [
            SimulationEvent(
                event_id=f"krx-daily-open:{prefix}",
                available_time_utc=open_time,
                event_time_utc=open_time,
                venue="KRX",
                event_type="quote",
                provider_sequence=None,
                payload=open_quote,
            ),
            SimulationEvent(
                event_id=f"krx-daily-close:{prefix}",
                available_time_utc=close_time,
                event_time_utc=close_time,
                venue="KRX",
                event_type="quote",
                provider_sequence=None,
                payload=close_quote,
            ),
        ],
        [entry_order, exit_order],
    )


def _order(
    *,
    order_id: str,
    side: OrderSide,
    price: Decimal,
    quantity: Decimal,
    created_at_utc: int,
    expires_at_utc: int,
) -> OrderIntent:
    return OrderIntent(
        order_id=order_id,
        signal_id=order_id.replace("order", "signal"),
        strategy_id="h1_close_rebalance_daily_proxy",
        legs=[
            OrderLeg(
                leg_id=f"leg:{order_id}",
                instrument_id="KRX_000660_COMMON_STOCK",
                venue=Venue.KRX,
                side=side,
                quantity=quantity,
                limit_price=price,
                time_in_force=TimeInForce.DAY,
            )
        ],
        risk_budget=price * quantity,
        created_at_utc=created_at_utc,
        expires_at_utc=expires_at_utc,
        idempotency_key=f"idem:{order_id}",
    )


def _quote(bar: Bar, price: Decimal, event_time_utc: int) -> MarketQuote:
    depth = max(bar.volume, Decimal("1"))
    return MarketQuote(
        source="krx_daily_research_replay",
        venue=Venue.KRX,
        symbol=bar.symbol,
        event_time_utc=event_time_utc,
        received_time_utc=event_time_utc,
        currency=Currency.KRW,
        session=Session.REFERENCE,
        is_delayed=True,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id=bar.instrument_id,
        bid_price=price,
        ask_price=price,
        bid_size=depth,
        ask_size=depth,
        quality_flag=[],
    )


def _statistics(
    trades: list[TradeResult], *, config: DailyProxyBacktestConfig, seed: int
) -> ScenarioStatistics:
    ordered = sorted(trades, key=lambda item: (item.trading_date, item.trade_id))
    daily_pnls = {item.trading_date: item.pnl for item in ordered}
    max_drawdown = compute_max_drawdown(ordered)
    return ScenarioStatistics(
        trade_count=len(ordered),
        cumulative_pnl=sum((item.pnl for item in ordered), Decimal("0")),
        expectancy=compute_expectancy(ordered),
        median_trade_pnl=compute_median_trade_pnl(ordered),
        win_rate=compute_win_rate(ordered),
        profit_factor=compute_profit_factor(ordered),
        max_drawdown=max_drawdown,
        mdd_pct=max_drawdown / config.initial_capital * Decimal("100"),
        top_1_day_profit_share=top_n_day_profit_share(daily_pnls, 1),
        bootstrap_expectancy_ci=bootstrap_confidence_interval(
            [item.pnl for item in ordered],
            n_resamples=config.bootstrap_resamples,
            confidence=config.confidence,
            seed=seed,
        ),
        permutation_p_value=Decimal(
            str(
                date_permutation_p_value(
                    daily_pnls,
                    n_permutations=config.permutation_count,
                    seed=seed,
                )
            )
        ),
    )


def _required_turnover(bar: Bar) -> Decimal:
    if bar.turnover is None or bar.turnover <= 0:
        raise ValueError(f"{bar.symbol} 20일 ADV 입력 거래대금이 없음")
    return bar.turnover


def _data_snapshot_hash(
    bars: tuple[StoredKrxDailyBar, ...], etp: tuple[StoredKrxEtpSnapshot, ...]
) -> str:
    payload = {
        "bars": [
            {
                "normalized_record_id": item.normalized_record_id,
                "created_at_utc": item.created_at_utc,
                "payload": item.bar.model_dump(mode="json"),
            }
            for item in bars
        ],
        "etp": [
            {
                "normalized_record_id": item.normalized_record_id,
                "created_at_utc": item.created_at_utc,
                "payload": item.snapshot.model_dump(mode="json"),
            }
            for item in etp
        ],
    }
    return _canonical_hash(payload)


def _result_payload(
    *,
    data_snapshot_hash: str,
    bar_count: int,
    etp_snapshot_count: int,
    available_feature_count: int,
    chronological_splits: tuple[TimeSplit, ...],
    folds: tuple[WalkForwardFoldResult, ...],
    aggregate_base: ScenarioStatistics,
    aggregate_stress_2x: ScenarioStatistics,
    promotion: PromotionResult,
    config: DailyProxyBacktestConfig,
) -> dict[str, object]:
    return {
        "model_version": KRX_DAILY_PROXY_MODEL_VERSION,
        "data_resolution": KRX_DAILY_PROXY_DATA_RESOLUTION,
        "promotion_scope": KRX_DAILY_PROXY_PROMOTION_SCOPE,
        "promotion_eligible": False,
        "kappa_policy": "explicit-fixed-research-parameter",
        "data_snapshot_hash": data_snapshot_hash,
        "bar_count": bar_count,
        "etp_snapshot_count": etp_snapshot_count,
        "available_feature_count": available_feature_count,
        "chronological_splits": [_split_dict(item) for item in chronological_splits],
        "folds": [item.to_dict() for item in folds],
        "aggregate_base": aggregate_base.to_dict(),
        "aggregate_stress_2x": aggregate_stress_2x.to_dict(),
        "promotion": {
            "verdict": promotion.verdict.value,
            "reasons": list(promotion.reasons),
            "model_version": promotion.model_version,
            "data_resolution": promotion.data_resolution,
            "promotion_scope": promotion.promotion_scope,
            "promotion_eligible": promotion.promotion_eligible,
        },
        "config": {
            "seed": config.seed,
            "trading_days": config.trading_days,
            "kappa": str(config.kappa),
            "neutral_band": str(config.neutral_band),
            "order_quantity": str(config.order_quantity),
            "initial_capital": str(config.initial_capital),
            "commission_rate": str(config.commission_rate),
            "tax_rate": str(config.tax_rate),
            "market_impact_coefficient": str(config.market_impact_coefficient),
            "bootstrap_resamples": config.bootstrap_resamples,
            "permutation_count": config.permutation_count,
            "confidence": config.confidence,
            "min_sample_size": config.min_sample_size,
        },
    }


def _split_dict(split: TimeSplit) -> dict[str, str]:
    return {"name": split.name, "start": split.start.isoformat(), "end": split.end.isoformat()}


def _canonical_hash(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
