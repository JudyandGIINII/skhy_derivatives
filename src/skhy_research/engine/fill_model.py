"""P1-05 최소 체결 모델: 단일 다리 지정가, 참여율 기반 부분체결 (FR-12).

주문 생성 시각 이후에 수신된 호가에서만 체결한다(미래 호가 체결 금지).
거래정지·시장휴장 플래그가 있는 호가는 체결에 사용하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from skhy_research.domain.enums import OrderSide, OrderStatus, QualityFlag
from skhy_research.domain.execution import OrderIntent, PaperFill
from skhy_research.domain.market import MarketQuote

FILL_MODEL_VERSION = "min_participation@1.0.0"

_BLOCKING_QUALITY_FLAGS = frozenset({QualityFlag.HALTED, QualityFlag.MARKET_CLOSED})


@dataclass
class OrderState:
    order: OrderIntent
    leg_index: int
    remaining_quantity: Decimal
    fills: list[PaperFill] = field(default_factory=list)

    @property
    def is_done(self) -> bool:
        return self.remaining_quantity <= 0


def try_fill_leg(
    order_state: OrderState,
    quote: MarketQuote,
    max_participation_rate: Decimal,
    fill_id_factory: Callable[[], str],
) -> PaperFill | None:
    leg = order_state.order.legs[order_state.leg_index]

    if quote.event_time_utc <= order_state.order.created_at_utc:
        return None  # 미래 호가 금지: 주문 생성 이후 호가만 사용
    if quote.event_time_utc > order_state.order.expires_at_utc:
        return None
    if set(quote.quality_flag) & _BLOCKING_QUALITY_FLAGS:
        return None

    marketable = (
        quote.ask_price <= leg.limit_price
        if leg.side == OrderSide.BUY
        else quote.bid_price >= leg.limit_price
    )
    if not marketable:
        return None

    available_size = quote.ask_size if leg.side == OrderSide.BUY else quote.bid_size
    fillable = min(order_state.remaining_quantity, available_size * max_participation_rate)
    if fillable <= 0:
        return None

    fill_price = quote.ask_price if leg.side == OrderSide.BUY else quote.bid_price
    slippage = (
        (fill_price - leg.limit_price)
        if leg.side == OrderSide.BUY
        else (leg.limit_price - fill_price)
    )

    order_state.remaining_quantity -= fillable
    status = OrderStatus.FILLED if order_state.is_done else OrderStatus.PARTIALLY_FILLED
    fill = PaperFill(
        fill_id=fill_id_factory(),
        order_id=order_state.order.order_id,
        leg_id=leg.leg_id,
        filled_quantity=fillable,
        unfilled_quantity=order_state.remaining_quantity,
        fill_price=fill_price,
        used_market_event_ids=[],
        slippage=slippage,
        fill_model_version=FILL_MODEL_VERSION,
        filled_at_utc=quote.event_time_utc,
        status=status,
    )
    order_state.fills.append(fill)
    return fill
