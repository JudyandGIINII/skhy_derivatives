"""P1-05 완료조건: 미래 호가 체결 0건, 부분체결, 정지 시 체결 차단."""

from __future__ import annotations

from decimal import Decimal

from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    OrderSide,
    QualityFlag,
    Session,
    TimeInForce,
    Venue,
)
from skhy_research.domain.execution import OrderIntent, OrderLeg
from skhy_research.domain.market import MarketQuote
from skhy_research.engine.fill_model import OrderState, try_fill_leg

_T0 = 1_800_000_000_000_000_000


def _order(created_at: int, expires_at: int, side: OrderSide = OrderSide.BUY, limit: str = "100") -> OrderIntent:
    return OrderIntent(
        order_id="order-1",
        signal_id="sig-1",
        strategy_id="h1_close_rebalance",
        legs=[
            OrderLeg(
                leg_id="leg-1",
                instrument_id="000660",
                venue=Venue.KRX,
                side=side,
                quantity=Decimal("100"),
                limit_price=Decimal(limit),
                time_in_force=TimeInForce.DAY,
            )
        ],
        risk_budget=Decimal("1000000"),
        created_at_utc=created_at,
        expires_at_utc=expires_at,
        idempotency_key="idem-1",
    )


def _quote(event_time_utc: int, bid: str, ask: str, bid_size: str = "1000", ask_size: str = "1000", quality_flag=None) -> MarketQuote:
    return MarketQuote(
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
        bid_size=Decimal(bid_size),
        ask_size=Decimal(ask_size),
        quality_flag=quality_flag or [],
    )


def _state(order: OrderIntent) -> OrderState:
    return OrderState(order=order, leg_index=0, remaining_quantity=order.legs[0].quantity)


def test_quote_at_or_before_order_creation_never_fills() -> None:
    """완료조건: 미래 호가 체결 0건 — 사실은 '주문 생성 이전/동시 호가'가 체결에 쓰이지 않아야 한다."""
    order = _order(created_at=_T0, expires_at=_T0 + 100_000_000_000)
    state = _state(order)
    quote_before = _quote(event_time_utc=_T0 - 1, bid="99", ask="100")
    quote_at_creation = _quote(event_time_utc=_T0, bid="99", ask="100")

    assert try_fill_leg(state, quote_before, Decimal("1.0"), lambda: "f1") is None
    assert try_fill_leg(state, quote_at_creation, Decimal("1.0"), lambda: "f2") is None
    assert state.fills == []


def test_quote_after_expiry_does_not_fill() -> None:
    order = _order(created_at=_T0, expires_at=_T0 + 1000)
    state = _state(order)
    quote = _quote(event_time_utc=_T0 + 2000, bid="99", ask="100")

    assert try_fill_leg(state, quote, Decimal("1.0"), lambda: "f1") is None


def test_halted_quote_does_not_fill() -> None:
    order = _order(created_at=_T0, expires_at=_T0 + 100_000_000_000)
    state = _state(order)
    quote = _quote(event_time_utc=_T0 + 1, bid="99", ask="100", quality_flag=[QualityFlag.HALTED])

    assert try_fill_leg(state, quote, Decimal("1.0"), lambda: "f1") is None


def test_non_marketable_quote_does_not_fill() -> None:
    order = _order(created_at=_T0, expires_at=_T0 + 100_000_000_000, limit="100")
    state = _state(order)
    quote = _quote(event_time_utc=_T0 + 1, bid="99", ask="105")  # ask(105) > limit(100), 매수 불가

    assert try_fill_leg(state, quote, Decimal("1.0"), lambda: "f1") is None


def test_marketable_quote_partially_fills_bounded_by_participation_rate() -> None:
    order = _order(created_at=_T0, expires_at=_T0 + 100_000_000_000, limit="100")
    state = _state(order)  # remaining=100
    quote = _quote(event_time_utc=_T0 + 1, bid="99", ask="100", ask_size="50")

    fill = try_fill_leg(state, quote, Decimal("0.5"), lambda: "f1")

    assert fill is not None
    assert fill.filled_quantity == Decimal("25")  # min(100, 50*0.5)
    assert state.remaining_quantity == Decimal("75")


def test_order_fully_fills_across_multiple_quotes() -> None:
    order = _order(created_at=_T0, expires_at=_T0 + 100_000_000_000, limit="100")
    state = _state(order)  # remaining=100

    q1 = _quote(event_time_utc=_T0 + 1, bid="99", ask="100", ask_size="60")
    q2 = _quote(event_time_utc=_T0 + 2, bid="99", ask="100", ask_size="60")

    fill1 = try_fill_leg(state, q1, Decimal("1.0"), lambda: "f1")
    fill2 = try_fill_leg(state, q2, Decimal("1.0"), lambda: "f2")

    assert fill1 is not None
    assert fill2 is not None
    assert fill1.filled_quantity == Decimal("60")
    assert fill2.filled_quantity == Decimal("40")  # 잔여분만
    assert state.is_done is True
    assert fill2.status.value == "FILLED"


def test_sell_side_uses_bid_price_and_marketability() -> None:
    order = _order(created_at=_T0, expires_at=_T0 + 100_000_000_000, side=OrderSide.SELL, limit="100")
    state = _state(order)
    quote = _quote(event_time_utc=_T0 + 1, bid="101", ask="102", bid_size="200")

    fill = try_fill_leg(state, quote, Decimal("1.0"), lambda: "f1")

    assert fill is not None
    assert fill.fill_price == Decimal("101")
