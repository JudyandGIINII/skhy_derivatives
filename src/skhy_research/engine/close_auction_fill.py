"""H1 KRX 종가 경매 체결 모델 (PRD 9.1: 15:10~15:20 지정가 진입, 종가 경매로 종료).

KRX 종가 경매는 단일가 매매다. 연속 호가 체결모델(`fill_model.py`)과 달리
지정가가 경매가를 만족하면 전량 체결, 아니면 전량 미체결로 단순화한
최소판이다(부분체결·경매 물량 소진 모델링은 후속 Phase 확장 대상).
"""

from __future__ import annotations

from decimal import Decimal

from skhy_research.domain.enums import OrderSide, OrderStatus
from skhy_research.domain.execution import OrderIntent, PaperFill

CLOSE_AUCTION_FILL_MODEL_VERSION = "krx_close_auction@1.0.0"


def fill_at_close_auction(
    order: OrderIntent, auction_price: Decimal, auction_time_utc: int, fill_id: str
) -> PaperFill | None:
    leg = order.legs[0]
    marketable = (
        leg.limit_price >= auction_price
        if leg.side == OrderSide.BUY
        else leg.limit_price <= auction_price
    )
    if not marketable:
        return None

    return PaperFill(
        fill_id=fill_id,
        order_id=order.order_id,
        leg_id=leg.leg_id,
        filled_quantity=leg.quantity,
        unfilled_quantity=Decimal("0"),
        fill_price=auction_price,
        used_market_event_ids=[],
        slippage=Decimal("0"),  # 단일가 매매는 지정가-체결가 괴리를 슬리피지로 취급하지 않는다
        fill_model_version=CLOSE_AUCTION_FILL_MODEL_VERSION,
        filled_at_utc=auction_time_utc,
        status=OrderStatus.FILLED,
    )
