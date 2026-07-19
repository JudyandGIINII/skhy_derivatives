"""KIS/Toss live snapshot -> H1 feature -> strategy -> engine 계약 통합 검증."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from skhy_research.application.h1_live_snapshot import (
    H1_LIVE_FULL_MODEL_VERSION,
    H1_LIVE_REDUCED_MODEL_VERSION,
    H1LiveFundInput,
    H1LiveInputError,
    KappaRegimeEstimate,
    KrxPreviousCloseReference,
    build_h1_live_feature,
)
from skhy_research.application.h1_original_validation import (
    H1OriginalBacktestConfig,
    H1OriginalReplayDay,
    run_h1_original_backtest,
)
from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    MarketDataFeedMode,
    OrderSide,
    PromotionVerdict,
    ReplicationType,
    Session,
    TimeInForce,
    Venue,
)
from skhy_research.domain.execution import OrderIntent, OrderLeg
from skhy_research.domain.market import (
    IndicativeValueKind,
    MarketPriceSnapshot,
    MarketQuote,
    ObservationTimeSource,
    PublicationTimeSource,
)
from skhy_research.domain.reference import FundSnapshot
from skhy_research.domain.simulation_event import SimulationEvent
from skhy_research.engine.backtest import run_backtest
from skhy_research.features.h1_close_pressure.close_pressure import (
    ORIGINAL_H1_LIVE_DATA_RESOLUTION,
    ORIGINAL_H1_PROMOTION_SCOPE,
)
from skhy_research.features.h1_close_pressure.observable_flow import (
    FlowObservation,
    ObservableFlowField,
    ObservableFlowInput,
    ReplicationFlowEvidence,
)
from skhy_research.ports.market_data import MarketSnapshotBatch
from skhy_research.strategies.h1_close_rebalance.decision_window import build_decision_window
from skhy_research.strategies.h1_close_rebalance.strategy import (
    NO_SIGNAL_MISSING_REQUIRED_FLOW,
    H1CloseRebalanceStrategy,
)


def _snapshot(
    provider: str,
    instrument_id: str,
    symbol: str,
    price: str,
    *,
    event_time: int,
    received_time: int,
    nav: str | None = None,
) -> MarketPriceSnapshot:
    return MarketPriceSnapshot(
        record_id=f"{provider}:{symbol}:{event_time}",
        source=f"{provider.upper()}_PROD_REST",
        venue=Venue.KRX,
        symbol=symbol,
        event_time_utc=event_time,
        received_time_utc=received_time,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id=instrument_id,
        last_price=Decimal(price),
        published_time_utc=received_time,
        observation_time_source=ObservationTimeSource.PROVIDER_TIMESTAMP,
        publication_time_source=PublicationTimeSource.CLIENT_RECEIVED_AT,
        feed_mode=MarketDataFeedMode.LIVE,
        indicative_value=Decimal(nav) if nav is not None else None,
        indicative_value_kind=IndicativeValueKind.NAV if nav is not None else None,
        indicative_value_observed_at_utc=event_time if nav is not None else None,
    )


@pytest.mark.integration
def test_original_h1_consumes_guarded_live_snapshot_and_reuses_engine_contract() -> None:
    trading_date = date(2026, 7, 16)
    window = build_decision_window(trading_date, "15:10:00", "15:19:30")
    observed_at = window.signal_snapshot_utc
    decision_time = observed_at + 5_000_000_000
    underlying_id = "KRX_000660_COMMON_STOCK"
    fund_id = "KRX_0193T0_LEVERAGED_ETF"

    primary_snapshots = (
        _snapshot(
            "kis", underlying_id, "000660", "102", event_time=observed_at, received_time=observed_at + 1_000_000_000
        ),
        _snapshot(
            "kis",
            fund_id,
            "0193T0",
            "14.5",
            event_time=observed_at,
            received_time=observed_at + 2_000_000_000,
            nav="14.4",
        ),
    )
    secondary_snapshots = (
        _snapshot(
            "toss",
            underlying_id,
            "000660",
            "101.9",
            event_time=observed_at + 100_000_000,
            received_time=observed_at + 3_000_000_000,
        ),
        _snapshot(
            "toss",
            fund_id,
            "0193T0",
            "14.49",
            event_time=observed_at + 200_000_000,
            received_time=observed_at + 3_000_000_000,
        ),
    )
    primary = MarketSnapshotBatch("kis", observed_at, observed_at + 2_000_000_000, primary_snapshots)
    secondary = MarketSnapshotBatch(
        "toss", observed_at, observed_at + 3_000_000_000, secondary_snapshots
    )
    references = [
        KrxPreviousCloseReference(
            underlying_id,
            date(2026, 7, 15),
            Decimal("100"),
            observed_at - 1,
            "krx-underlying-close",
            Decimal("30"),
        ),
        KrxPreviousCloseReference(
            fund_id,
            date(2026, 7, 15),
            Decimal("14"),
            observed_at - 1,
            "krx-fund-close",
            Decimal("60"),
        ),
    ]
    prior_snapshot_time = observed_at - 86_400_000_000_000
    prior_fund_snapshot = FundSnapshot(
        source="krx_etp_reference",
        venue=Venue.KRX,
        symbol="0193T0",
        event_time_utc=prior_snapshot_time,
        received_time_utc=prior_snapshot_time,
        currency=Currency.KRW,
        session=Session.REFERENCE,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        fund_id=fund_id,
        leverage_beta=Decimal("2"),
        aum=Decimal("1000000000"),
        nav=Decimal("14"),
        net_creation_estimate=Decimal("2000000"),
        net_creation_estimate_method="shares_outstanding_delta_x_prior_nav",
        replication_type=ReplicationType.PHYSICAL,
        published_at=prior_snapshot_time,
        effective_at=prior_snapshot_time,
    )
    complete_flow = ObservableFlowInput(
        close_auction_imbalance_notional=FlowObservation(
            Decimal("1000000"), decision_time - 1, "krx-close-auction-imbalance"
        ),
        program_net_buy_notional=FlowObservation(
            Decimal("-2000000"), decision_time - 1, "krx-program-net-buy"
        ),
        net_creation_redemption_notional=FlowObservation(
            Decimal("2000000"), decision_time - 1, "krx-creation-redemption-estimate"
        ),
        replication=ReplicationFlowEvidence(
            ReplicationType.PHYSICAL,
            Decimal("0.5"),
            decision_time - 1,
            "issuer-replication-method",
        ),
    )
    fund_input = H1LiveFundInput(
        prior_fund_snapshot=prior_fund_snapshot,
        fund_snapshot_record_id="krx-prior-fund-snapshot",
        kappa_regime=KappaRegimeEstimate(
            value=Decimal("1"),
            regime="domestic_single_stock_product",
            fitted_through_date=date(2026, 7, 15),
            available_at_utc=decision_time - 1,
            input_record_id="h1-kappa-training-output",
            model_version="h1-kappa-regime-test-fixture-v1",
        ),
        observable_flow=complete_flow,
    )
    feature = build_h1_live_feature(
        [fund_input],
        primary,
        secondary,
        references,
        underlying_instrument_id=underlying_id,
        underlying_20d_adv_notional=Decimal("1000000000"),
        trading_date=trading_date,
        decision_window=window,
        decision_time_utc=decision_time,
        max_snapshot_age_ns=60_000_000_000,
        max_source_time_skew_ns=5_000_000_000,
        max_cross_source_divergence_pct=Decimal("1"),
    )

    assert feature.close_pressure.value == Decimal("0.04")
    assert feature.close_pressure.model_version == H1_LIVE_FULL_MODEL_VERSION
    assert feature.data_resolution == ORIGINAL_H1_LIVE_DATA_RESOLUTION
    assert feature.promotion_scope == ORIGINAL_H1_PROMOTION_SCOPE
    assert feature.promotion_eligible is True
    assert feature.indicative_value_evidence[0].consumed_by_close_pressure is False

    missing_auction_flow = replace(
        complete_flow,
        close_auction_imbalance_notional=FlowObservation(
            None,
            None,
            None,
            "G-03 종가 예상체결 피드 미확보",
        ),
    )
    reduced_feature = build_h1_live_feature(
        [replace(fund_input, observable_flow=missing_auction_flow)],
        primary,
        secondary,
        references,
        underlying_instrument_id=underlying_id,
        underlying_20d_adv_notional=Decimal("1000000000"),
        trading_date=trading_date,
        decision_window=window,
        decision_time_utc=decision_time,
        max_snapshot_age_ns=60_000_000_000,
        max_source_time_skew_ns=5_000_000_000,
        max_cross_source_divergence_pct=Decimal("1"),
    )
    assert reduced_feature.model_version == H1_LIVE_REDUCED_MODEL_VERSION
    assert reduced_feature.close_pressure.value == Decimal("0.04")
    assert reduced_feature.promotion_eligible is False
    assert reduced_feature.fund_features[0].missing_flow_fields == (
        ObservableFlowField.CLOSE_AUCTION_IMBALANCE.value,
    )

    strategy = H1CloseRebalanceStrategy(
        strategy_version=H1_LIVE_FULL_MODEL_VERSION,
        neutral_band=Decimal("0.001"),
    )
    reduced_decision = strategy.decide(
        instrument_id=underlying_id,
        feature_set_id=H1_LIVE_REDUCED_MODEL_VERSION,
        close_pressure=reduced_feature.close_pressure,
        input_record_ids=list(reduced_feature.input_record_ids),
        fund_snapshots_used=[prior_fund_snapshot],
        decision_time_utc=reduced_feature.decision_time_utc,
        expires_at_utc=window.order_intent_cutoff_utc,
        signal_id="signal-h1-missing-g03",
        estimated_cost=Decimal("0.001"),
        live_snapshots_used=list(reduced_feature.live_snapshots_used),
    )
    assert reduced_decision.signal is None
    assert reduced_decision.no_signal_reason == NO_SIGNAL_MISSING_REQUIRED_FLOW

    decision = strategy.decide(
        instrument_id=underlying_id,
        feature_set_id=H1_LIVE_FULL_MODEL_VERSION,
        close_pressure=feature.close_pressure,
        input_record_ids=list(feature.input_record_ids),
        fund_snapshots_used=[prior_fund_snapshot],
        decision_time_utc=feature.decision_time_utc,
        expires_at_utc=window.order_intent_cutoff_utc,
        signal_id="signal-h1-live",
        estimated_cost=Decimal("0.001"),
        live_snapshots_used=list(feature.live_snapshots_used),
    )
    assert decision.signal is not None
    assert decision.explain["data_resolution"] == ORIGINAL_H1_LIVE_DATA_RESOLUTION

    order = OrderIntent(
        order_id="order-h1-live",
        signal_id=decision.signal.signal_id,
        strategy_id=decision.signal.strategy_id,
        legs=[
            OrderLeg(
                leg_id="leg-h1-live",
                instrument_id=underlying_id,
                venue=Venue.KRX,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                limit_price=Decimal("103"),
                time_in_force=TimeInForce.DAY,
            )
        ],
        risk_budget=Decimal("103"),
        created_at_utc=decision_time,
        expires_at_utc=window.order_intent_cutoff_utc,
        idempotency_key="idem-h1-live",
    )
    execution_quote = MarketQuote(
        source="TOSS_PROD_REST",
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=decision_time + 1,
        received_time_utc=decision_time + 1,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id=underlying_id,
        bid_price=Decimal("101.9"),
        ask_price=Decimal("102.1"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
    )
    engine_result = run_backtest(
        [
            SimulationEvent(
                event_id="event-h1-live-quote",
                available_time_utc=decision_time + 1,
                event_time_utc=decision_time + 1,
                venue="KRX",
                event_type="quote",
                provider_sequence=None,
                payload=execution_quote,
            )
        ],
        [order],
        max_participation_rate=Decimal("1"),
        seed=7,
    )
    assert len(engine_result.fills) == 1
    assert engine_result.fills[0].fill_price == Decimal("102.1")

    exit_quote = MarketQuote(
        source="TOSS_PROD_REST",
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=window.order_intent_cutoff_utc + 1,
        received_time_utc=window.order_intent_cutoff_utc + 1,
        currency=Currency.KRW,
        session=Session.CLOSE_AUCTION,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id=underlying_id,
        bid_price=Decimal("103.0"),
        ask_price=Decimal("103.2"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
    )
    validation = run_h1_original_backtest(
        [
            H1OriginalReplayDay(
                trading_date=trading_date,
                feature=feature,
                fund_snapshots_used=(prior_fund_snapshot,),
                entry_quote=execution_quote,
                exit_quote=exit_quote,
            )
        ],
        config=H1OriginalBacktestConfig(bootstrap_resamples=20, permutations=20),
    )
    assert validation.base.trade_count == 1
    assert validation.base_long.trade_count == 1
    assert validation.base_short.trade_count == 0
    assert validation.stress_2x.trade_count == 1
    assert validation.promotion.verdict is PromotionVerdict.HOLD
    assert "거래일 부족" in validation.promotion.reasons[0]

    same_day_post_close = prior_fund_snapshot.model_copy(
        update={"effective_at": window.order_intent_cutoff_utc + 2_400_000_000_000}
    )
    with pytest.raises(H1LiveInputError, match="전일 확정치"):
        build_h1_live_feature(
            [replace(fund_input, prior_fund_snapshot=same_day_post_close)],
            primary,
            secondary,
            references,
            underlying_instrument_id=underlying_id,
            underlying_20d_adv_notional=Decimal("1000000000"),
            trading_date=trading_date,
            decision_window=window,
            decision_time_utc=decision_time,
            max_snapshot_age_ns=60_000_000_000,
            max_source_time_skew_ns=5_000_000_000,
            max_cross_source_divergence_pct=Decimal("1"),
        )

    same_day_fitted_kappa = replace(
        fund_input.kappa_regime,
        fitted_through_date=trading_date,
    )
    with pytest.raises(H1LiveInputError, match="학습 구간 이후"):
        build_h1_live_feature(
            [replace(fund_input, kappa_regime=same_day_fitted_kappa)],
            primary,
            secondary,
            references,
            underlying_instrument_id=underlying_id,
            underlying_20d_adv_notional=Decimal("1000000000"),
            trading_date=trading_date,
            decision_window=window,
            decision_time_utc=decision_time,
            max_snapshot_age_ns=60_000_000_000,
            max_source_time_skew_ns=5_000_000_000,
            max_cross_source_divergence_pct=Decimal("1"),
        )
