"""P1-05 완료조건: 동일 run 2회의 event/result hash가 일치하고, 미래 호가 체결이 0건이다."""

from __future__ import annotations

from decimal import Decimal

from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    OrderSide,
    Session,
    TimeInForce,
    Venue,
)
from skhy_research.domain.execution import OrderIntent, OrderLeg
from skhy_research.domain.market import MarketQuote
from skhy_research.domain.simulation_event import SimulationEvent
from skhy_research.engine.backtest import run_backtest

_T0 = 1_800_000_000_000_000_000


def _quote_event(event_id: str, event_time_utc: int, bid: str, ask: str) -> SimulationEvent:
    quote = MarketQuote(
        source="kis",
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=event_time_utc,
        received_time_utc=event_time_utc,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id="000660",
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=Decimal("1000"),
        ask_size=Decimal("1000"),
    )
    return SimulationEvent(
        event_id=event_id,
        available_time_utc=event_time_utc,
        event_time_utc=event_time_utc,
        venue="KRX",
        event_type="quote",
        provider_sequence=None,
        payload=quote,
    )


def _build_scenario() -> tuple[list[SimulationEvent], list[OrderIntent]]:
    order_created_at = _T0
    events = [
        _quote_event("evt-before-1", _T0 - 3000, bid="98", ask="99"),  # 주문 생성 이전 — 체결 금지
        _quote_event("evt-before-2", _T0 - 1000, bid="98", ask="99"),  # 주문 생성 이전 — 체결 금지
        _quote_event("evt-after-1", _T0 + 1000, bid="99", ask="100"),
        _quote_event("evt-after-2", _T0 + 2000, bid="99", ask="100"),
    ]
    order = OrderIntent(
        order_id="order-1",
        signal_id="sig-1",
        strategy_id="h1_close_rebalance",
        legs=[
            OrderLeg(
                leg_id="leg-1",
                instrument_id="000660",
                venue=Venue.KRX,
                side=OrderSide.BUY,
                quantity=Decimal("100"),
                limit_price=Decimal("100"),
                time_in_force=TimeInForce.DAY,
            )
        ],
        risk_budget=Decimal("1000000"),
        created_at_utc=order_created_at,
        expires_at_utc=_T0 + 100_000_000_000,
        idempotency_key="idem-1",
    )
    return events, [order]


def test_running_twice_yields_identical_event_journal_hash_and_fills() -> None:
    events, orders = _build_scenario()

    result_a = run_backtest(events, orders, max_participation_rate=Decimal("1.0"), seed=42)
    result_b = run_backtest(events, orders, max_participation_rate=Decimal("1.0"), seed=42)

    assert result_a.event_journal_hash == result_b.event_journal_hash
    assert [f.filled_quantity for f in result_a.fills] == [f.filled_quantity for f in result_b.fills]
    assert result_a.portfolio.positions == result_b.portfolio.positions
    assert result_a.portfolio.realized_pnl == result_b.portfolio.realized_pnl


def test_no_fills_use_quotes_before_or_at_order_creation() -> None:
    events, orders = _build_scenario()
    result = run_backtest(events, orders, max_participation_rate=Decimal("1.0"), seed=42)

    order_created_at = orders[0].created_at_utc
    assert all(fill.filled_at_utc > order_created_at for fill in result.fills)


def test_event_journal_preserves_deterministic_order() -> None:
    events, orders = _build_scenario()
    result = run_backtest(events, orders, max_participation_rate=Decimal("1.0"), seed=42)

    event_times = [int(entry["event_time_utc"]) for entry in result.event_journal]  # type: ignore[arg-type]
    assert event_times == sorted(event_times)


def test_order_fully_filled_from_post_creation_quotes_only() -> None:
    events, orders = _build_scenario()
    result = run_backtest(events, orders, max_participation_rate=Decimal("1.0"), seed=42)

    total_filled = sum((f.filled_quantity for f in result.fills), Decimal("0"))
    assert total_filled == Decimal("100")
    assert result.portfolio.positions["000660"] == Decimal("100")
