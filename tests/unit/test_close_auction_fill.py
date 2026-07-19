"""P1-06 검증: KRX 종가 경매 단일가 체결 모델."""

from __future__ import annotations

from decimal import Decimal

from skhy_research.domain.enums import OrderSide, TimeInForce, Venue
from skhy_research.domain.execution import OrderIntent, OrderLeg
from skhy_research.engine.close_auction_fill import (
    CLOSE_AUCTION_FILL_MODEL_VERSION,
    fill_at_close_auction,
)

_T0 = 1_800_000_000_000_000_000


def _order(side: OrderSide, limit: str, quantity: str = "100") -> OrderIntent:
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
                quantity=Decimal(quantity),
                limit_price=Decimal(limit),
                time_in_force=TimeInForce.DAY,
            )
        ],
        risk_budget=Decimal("1000000"),
        created_at_utc=_T0,
        expires_at_utc=_T0 + 100_000_000_000,
        idempotency_key="idem-1",
    )


def test_buy_fills_fully_when_limit_at_or_above_auction_price() -> None:
    order = _order(OrderSide.BUY, limit="200000")
    fill = fill_at_close_auction(order, auction_price=Decimal("199500"), auction_time_utc=_T0, fill_id="f1")

    assert fill is not None
    assert fill.filled_quantity == Decimal("100")
    assert fill.unfilled_quantity == Decimal("0")
    assert fill.fill_price == Decimal("199500")
    assert fill.fill_model_version == CLOSE_AUCTION_FILL_MODEL_VERSION


def test_buy_does_not_fill_when_limit_below_auction_price() -> None:
    order = _order(OrderSide.BUY, limit="199000")
    fill = fill_at_close_auction(order, auction_price=Decimal("199500"), auction_time_utc=_T0, fill_id="f1")
    assert fill is None


def test_sell_fills_fully_when_limit_at_or_below_auction_price() -> None:
    order = _order(OrderSide.SELL, limit="199000")
    fill = fill_at_close_auction(order, auction_price=Decimal("199500"), auction_time_utc=_T0, fill_id="f1")

    assert fill is not None
    assert fill.fill_price == Decimal("199500")


def test_sell_does_not_fill_when_limit_above_auction_price() -> None:
    order = _order(OrderSide.SELL, limit="200000")
    fill = fill_at_close_auction(order, auction_price=Decimal("199500"), auction_time_utc=_T0, fill_id="f1")
    assert fill is None


def test_fill_has_zero_slippage_for_single_price_auction() -> None:
    order = _order(OrderSide.BUY, limit="200000")
    fill = fill_at_close_auction(order, auction_price=Decimal("199500"), auction_time_utc=_T0, fill_id="f1")
    assert fill is not None
    assert fill.slippage == Decimal("0")
