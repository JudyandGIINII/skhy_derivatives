"""KRX fixture -> universe -> daily-proxy feature -> strategy -> promotion 분리 검증."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from skhy_research.application.h1_krx_daily_proxy import (
    KRX_DAILY_PROXY_MODEL_VERSION,
    KRX_DAILY_PROXY_PROMOTION_SCOPE,
    KrxDailyProxyFundInput,
    KrxDailyProxyMarketInput,
    build_krx_daily_proxy_feature,
)
from skhy_research.application.instrument_master import InstrumentMaster
from skhy_research.application.leverage_universe_discovery import (
    discover_and_register_krx_leveraged_universe,
)
from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    OrderSide,
    PromotionVerdict,
    Session,
    SignalDirection,
    TimeInForce,
    Venue,
)
from skhy_research.domain.execution import OrderIntent, OrderLeg
from skhy_research.domain.market import MarketQuote
from skhy_research.domain.simulation_event import SimulationEvent
from skhy_research.engine.backtest import run_backtest
from skhy_research.experiments.promotion import (
    PromotionCriteria,
    PromotionInput,
    evaluate_promotion,
)
from skhy_research.strategies.h1_close_rebalance.strategy import H1CloseRebalanceStrategy

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "krx"


class _FixtureKrxEtpClient:
    def fetch_daily_etf_trades(self, trading_date: date) -> list[dict[str, Any]]:
        assert trading_date == date(2026, 7, 16)
        return _load_rows("etf_daily_20260716.json")

    def fetch_daily_etn_trades(self, trading_date: date) -> list[dict[str, Any]]:
        assert trading_date == date(2026, 7, 16)
        return _load_rows("etn_daily_20260716.json")


def _load_rows(fixture_name: str) -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(
        (_FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8")
    )
    rows = payload["OutBlock_1"]
    assert isinstance(rows, list)
    return rows


def _load_proxy_fixture() -> dict[str, Any]:
    return json.loads(
        (_FIXTURE_ROOT / "h1_daily_proxy_20260716.json").read_text(encoding="utf-8")
    )


@pytest.mark.integration
def test_krx_daily_proxy_pipeline_is_lineaged_and_cannot_promote_original_h1() -> None:
    fixture = _load_proxy_fixture()
    basis_date = date.fromisoformat(str(fixture["basis_date"]))
    signal_date = date.fromisoformat(str(fixture["signal_date"]))
    received_at = int(fixture["received_at_utc"])
    as_of_time = int(fixture["as_of_time_utc"])

    discovery = discover_and_register_krx_leveraged_universe(
        _FixtureKrxEtpClient(),
        InstrumentMaster(),
        basis_date,
        target_underlyings=frozenset({"SK하이닉스"}),
    )
    fund_configs: dict[str, dict[str, Any]] = fixture["funds"]
    fund_inputs = [
        KrxDailyProxyFundInput.from_discovered_product(
            product,
            kappa=Decimal(str(fund_configs[product.source_symbol]["kappa"])),
            received_at_utc=received_at,
            input_record_ids=tuple(
                str(value)
                for value in fund_configs[product.source_symbol]["input_record_ids"]
            ),
        )
        for product in discovery.products
    ]

    underlying: dict[str, Any] = fixture["underlying"]
    market_input = KrxDailyProxyMarketInput(
        basis_date=basis_date,
        previous_close=Decimal(str(underlying["previous_close"])),
        close=Decimal(str(underlying["close"])),
        turnover_notional_20d=tuple(
            Decimal(str(value)) for value in underlying["turnover_notional_20d"]
        ),
        received_at_utc=received_at,
        input_record_ids=tuple(str(value) for value in underlying["input_record_ids"]),
    )
    feature = build_krx_daily_proxy_feature(
        fund_inputs,
        market_input,
        signal_date=signal_date,
        as_of_time_utc=as_of_time,
    )

    assert len(feature.fund_features) == 3
    assert len(feature.input_record_ids) == 4
    assert feature.close_pressure.value > Decimal("0")
    assert feature.close_pressure.promotion_eligible is False

    strategy = H1CloseRebalanceStrategy(
        strategy_version=KRX_DAILY_PROXY_MODEL_VERSION,
        neutral_band=Decimal("0.001"),
        promotion_scope=KRX_DAILY_PROXY_PROMOTION_SCOPE,
    )
    decision = strategy.decide(
        instrument_id="000660",
        feature_set_id=KRX_DAILY_PROXY_MODEL_VERSION,
        close_pressure=feature.close_pressure,
        input_record_ids=list(feature.input_record_ids),
        fund_snapshots_used=[],
        decision_time_utc=as_of_time,
        expires_at_utc=as_of_time + 100,
        signal_id="signal-krx-daily-proxy",
        estimated_cost=Decimal("0.001"),
    )
    assert decision.signal is not None
    assert decision.signal.strategy_version == KRX_DAILY_PROXY_MODEL_VERSION
    assert decision.signal.input_record_ids == list(feature.input_record_ids)
    assert decision.signal.direction == SignalDirection.LONG

    # KRX 일별 종가를 research-only synthetic quote로 만들어 기존 engine 계약 호환성만
    # 검증한다. 이는 실시간 KIS/Toss feed나 실행 가능 체결 증거가 아니다.
    close = Decimal(str(underlying["close"]))
    order = OrderIntent(
        order_id="order-krx-daily-proxy",
        signal_id=decision.signal.signal_id,
        strategy_id=decision.signal.strategy_id,
        legs=[
            OrderLeg(
                leg_id="leg-krx-daily-proxy",
                instrument_id="000660",
                venue=Venue.KRX,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                limit_price=close,
                time_in_force=TimeInForce.DAY,
            )
        ],
        risk_budget=close,
        created_at_utc=as_of_time,
        expires_at_utc=as_of_time + 100,
        idempotency_key="idem-krx-daily-proxy",
    )
    quote = MarketQuote(
        source="krx_daily_fixture",
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=as_of_time + 1,
        received_time_utc=as_of_time + 1,
        currency=Currency.KRW,
        session=Session.REFERENCE,
        is_delayed=True,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id="000660",
        bid_price=close,
        ask_price=close,
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
    )
    engine_result = run_backtest(
        [
            SimulationEvent(
                event_id="event-krx-daily-proxy",
                available_time_utc=as_of_time + 1,
                event_time_utc=as_of_time + 1,
                venue="KRX",
                event_type="quote",
                provider_sequence=None,
                payload=quote,
            )
        ],
        [order],
        max_participation_rate=Decimal("1"),
        seed=7,
    )
    assert len(engine_result.fills) == 1
    assert engine_result.fills[0].fill_price == close

    promotion = evaluate_promotion(
        PromotionInput(
            trade_count=100,
            expectancy=Decimal("1"),
            profit_factor=Decimal("2"),
            stress_cumulative_pnl=Decimal("100"),
            top_1_day_profit_share=Decimal("0.1"),
            mdd_pct=Decimal("1"),
            model_version=feature.model_version,
            data_resolution=feature.data_resolution,
            promotion_scope=feature.promotion_scope,
            promotion_eligible=feature.promotion_eligible,
        ),
        PromotionCriteria(
            min_expectancy=Decimal("0"),
            min_profit_factor=Decimal("1.2"),
            stress_min_cumulative_pnl=Decimal("0"),
            max_single_day_profit_share=Decimal("0.3"),
            max_strategy_mdd_pct=Decimal("5"),
            min_sample_size=30,
        ),
    )
    assert promotion.verdict == PromotionVerdict.HOLD
    assert promotion.model_version == KRX_DAILY_PROXY_MODEL_VERSION
    assert promotion.promotion_eligible is False
