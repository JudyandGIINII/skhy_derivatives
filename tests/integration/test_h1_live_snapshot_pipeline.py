"""KIS/Toss live snapshot -> H1 feature -> strategy -> engine 계약 통합 검증."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from skhy_research.application.h1_live_snapshot import (
    H1_LIVE_MODEL_VERSION,
    H1LiveFundInput,
    KrxPreviousCloseReference,
    build_h1_live_feature,
)
from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    MarketDataFeedMode,
    OrderSide,
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
from skhy_research.domain.simulation_event import SimulationEvent
from skhy_research.engine.backtest import run_backtest
from skhy_research.features.h1_close_pressure.close_pressure import (
    ORIGINAL_H1_LIVE_DATA_RESOLUTION,
    ORIGINAL_H1_PROMOTION_SCOPE,
)
from skhy_research.ports.market_data import MarketSnapshotBatch
from skhy_research.strategies.h1_close_rebalance.decision_window import build_decision_window
from skhy_research.strategies.h1_close_rebalance.strategy import H1CloseRebalanceStrategy


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
    feature = build_h1_live_feature(
        [
            H1LiveFundInput(
                fund_id=fund_id,
                beta=Decimal("2"),
                listed_notional_proxy=Decimal("1000000000"),
                kappa=Decimal("1"),
                observable_flow_adjustment=None,
                basis_date=date(2026, 7, 15),
                available_at_utc=observed_at - 1,
                input_record_ids=("krx-fund-listed-notional",),
            )
        ],
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
    assert feature.close_pressure.model_version == H1_LIVE_MODEL_VERSION
    assert feature.data_resolution == ORIGINAL_H1_LIVE_DATA_RESOLUTION
    assert feature.promotion_scope == ORIGINAL_H1_PROMOTION_SCOPE
    assert feature.promotion_eligible is True
    assert feature.indicative_value_evidence[0].consumed_by_close_pressure is False

    strategy = H1CloseRebalanceStrategy(
        strategy_version="h1_close_rebalance_live_v1",
        neutral_band=Decimal("0.001"),
    )
    decision = strategy.decide(
        instrument_id=underlying_id,
        feature_set_id=H1_LIVE_MODEL_VERSION,
        close_pressure=feature.close_pressure,
        input_record_ids=list(feature.input_record_ids),
        fund_snapshots_used=[],
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
