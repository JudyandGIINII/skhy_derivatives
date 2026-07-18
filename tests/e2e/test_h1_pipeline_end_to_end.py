"""P1-09: H1 feature -> strategy -> 체결 -> 통계 -> 승격판정 전체 파이프라인 연결 검증.

**중요**: 합성(synthetic) 데이터로 배선(wiring)이 올바른지만 검증한다. 실제
전략 유효성 검증(진짜 PASS/HOLD/REJECT 판정)은 Phase 3에서 실데이터·60거래일
전진관측으로 수행한다(PRD 11.1).
"""

from __future__ import annotations

import uuid
from datetime import date, time, timedelta
from decimal import Decimal

from skhy_research.adapters.calendars.static_holiday_provider import StaticHolidayProvider
from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.domain.calendar import local_datetime_to_utc_nanos
from skhy_research.domain.enums import (
    OrderSide,
    PromotionVerdict,
    SignalDirection,
    TimeInForce,
    Venue,
)
from skhy_research.domain.execution import OrderIntent, OrderLeg
from skhy_research.engine.close_auction_fill import fill_at_close_auction
from skhy_research.experiments.promotion import (
    PromotionCriteria,
    PromotionInput,
    evaluate_promotion,
)
from skhy_research.experiments.statistics import (
    TradeResult,
    compute_expectancy,
    compute_max_drawdown,
    compute_profit_factor,
    top_n_day_profit_share,
)
from skhy_research.features.h1_close_pressure.close_pressure import ClosePressureResult
from skhy_research.strategies.h1_close_rebalance.decision_window import build_decision_window
from skhy_research.strategies.h1_close_rebalance.strategy import H1CloseRebalanceStrategy

_INSTRUMENT_ID = "SKHY_000660_KRX_COMMON"


def _pick_trading_days(calendar_resolver: CalendarResolver, start: date, count: int) -> list[date]:
    trading_days: list[date] = []
    cursor = start
    while len(trading_days) < count:
        if calendar_resolver.is_trading_day(Venue.KRX, cursor):
            trading_days.append(cursor)
        cursor += timedelta(days=1)
    return trading_days


def test_h1_pipeline_from_close_pressure_to_promotion_verdict() -> None:
    calendar_resolver = CalendarResolver(StaticHolidayProvider())
    trading_days = _pick_trading_days(calendar_resolver, date(2026, 3, 2), count=10)

    # 결정론적 합성 압력값: 방향은 진동하지만 손익은 항상 우위가 있도록 구성(배선 검증용, 실제 우위 아님)
    pressures = [
        Decimal(v)
        for v in ("0.004", "-0.004", "0.005", "0.0005", "-0.005", "0.004", "0.006", "-0.006", "0.004", "0.005")
    ]

    strategy = H1CloseRebalanceStrategy(strategy_version="1.0.0", neutral_band=Decimal("0.001"))

    trades: list[TradeResult] = []
    daily_pnls: dict[date, Decimal] = {}

    for trading_date, pressure in zip(trading_days, pressures, strict=True):
        window = build_decision_window(trading_date, "15:10:00", "15:19:30")
        decision = strategy.decide(
            instrument_id=_INSTRUMENT_ID,
            feature_set_id="h1_close_pressure@1.0.0",
            close_pressure=ClosePressureResult(pressure, "full", ()),
            input_record_ids=[],
            fund_snapshots_used=[],  # lookahead 감사는 별도 테스트(test_h1_lookahead_lineage_audit.py)에서 다룬다
            decision_time_utc=window.signal_snapshot_utc,
            expires_at_utc=window.order_intent_cutoff_utc,
            signal_id=str(uuid.uuid4()),
            estimated_cost=Decimal("0.0005"),
        )
        if decision.signal is None:
            continue  # neutral band — 무신호

        signal = decision.signal
        side = OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL
        entry_price = Decimal("200000")
        # limit을 넉넉히 잡아 항상 marketable하게 만든다(체결 배선 자체를 검증하는 것이 목적)
        limit_price = (
            entry_price + Decimal("2000") if side == OrderSide.BUY else entry_price - Decimal("2000")
        )

        order = OrderIntent(
            order_id=f"order-{trading_date.isoformat()}",
            signal_id=signal.signal_id,
            strategy_id=strategy.strategy_id,
            legs=[
                OrderLeg(
                    leg_id="leg-1",
                    instrument_id=_INSTRUMENT_ID,
                    venue=Venue.KRX,
                    side=side,
                    quantity=Decimal("10"),
                    limit_price=limit_price,
                    time_in_force=TimeInForce.DAY,
                )
            ],
            risk_budget=Decimal("100000000"),
            created_at_utc=window.signal_snapshot_utc,
            expires_at_utc=window.order_intent_cutoff_utc + 1,
            idempotency_key=f"idem-{trading_date.isoformat()}",
        )
        auction_time = local_datetime_to_utc_nanos(trading_date, time(15, 25), Venue.KRX)
        fill = fill_at_close_auction(
            order,
            auction_price=entry_price,
            auction_time_utc=auction_time,
            fill_id=f"fill-{trading_date.isoformat()}",
        )
        assert fill is not None  # limit을 넉넉히 잡았으므로 항상 체결되어야 한다

        # 신호 방향과 압력 크기에 비례하는 결정론적 손익(합성 데이터, 실제 다음날 가격이 아니다)
        edge = abs(pressure) - signal.expected_cost
        pnl = entry_price * Decimal("10") * edge
        trades.append(TradeResult(trade_id=fill.fill_id, pnl=pnl, trading_date=trading_date))
        daily_pnls[trading_date] = daily_pnls.get(trading_date, Decimal("0")) + pnl

    assert len(trades) >= 5  # neutral band에 걸리지 않은 날이 충분해야 파이프라인 검증이 의미있다

    expectancy = compute_expectancy(trades)
    profit_factor = compute_profit_factor(trades)
    mdd = compute_max_drawdown(trades)
    top1_share = top_n_day_profit_share(daily_pnls, n=1)

    criteria = PromotionCriteria(
        min_expectancy=Decimal("0"),
        min_profit_factor=Decimal("1.2"),
        stress_min_cumulative_pnl=Decimal("0"),
        max_single_day_profit_share=Decimal("0.9"),  # 표본이 작아 집중도가 높을 수 있음을 감안
        max_strategy_mdd_pct=Decimal("999999"),  # 이 테스트는 파이프라인 배선만 검증(실제 % 스케일 아님)
        min_sample_size=3,
    )
    promotion_input = PromotionInput(
        trade_count=len(trades),
        expectancy=expectancy,
        profit_factor=profit_factor,
        stress_cumulative_pnl=sum((t.pnl for t in trades), Decimal("0")),
        top_1_day_profit_share=top1_share,
        mdd_pct=mdd,
    )
    result = evaluate_promotion(promotion_input, criteria)

    assert result.verdict in {PromotionVerdict.PASS, PromotionVerdict.HOLD, PromotionVerdict.REJECT}
    assert result.verdict == PromotionVerdict.PASS  # 모든 합성 신호가 이익이 나도록 구성했으므로 PASS여야 한다
