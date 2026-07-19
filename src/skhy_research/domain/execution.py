"""페이퍼 전용 주문·체결 타입 (PRD 8.2 OrderIntent, PaperFill)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skhy_research.domain.enums import Currency, OrderSide, OrderStatus, TimeInForce, Venue
from skhy_research.domain.market import EpochNanos, NonNegativeDecimal


class OrderLeg(BaseModel):
    model_config = ConfigDict(frozen=True)

    leg_id: str
    instrument_id: str
    venue: Venue
    side: OrderSide
    quantity: NonNegativeDecimal
    limit_price: NonNegativeDecimal
    time_in_force: TimeInForce


class OrderIntent(BaseModel):
    """페이퍼 전용. 실제 브로커 주문 엔드포인트를 호출하지 않는다 (PRD 7.3, 11장)."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    signal_id: str
    strategy_id: str
    legs: list[OrderLeg]
    hedge_ratio: Decimal | None = None
    risk_budget: NonNegativeDecimal
    created_at_utc: EpochNanos
    expires_at_utc: EpochNanos
    idempotency_key: str

    @model_validator(mode="after")
    def _has_at_least_one_leg(self) -> OrderIntent:
        if not self.legs:
            raise ValueError("OrderIntent는 최소 1개의 leg를 가져야 한다")
        return self

    @model_validator(mode="after")
    def _expiry_after_creation(self) -> OrderIntent:
        if self.expires_at_utc <= self.created_at_utc:
            raise ValueError("expires_at_utc는 created_at_utc보다 이후여야 한다")
        return self


class PaperFill(BaseModel):
    model_config = ConfigDict(frozen=True)

    fill_id: str
    order_id: str
    leg_id: str
    filled_quantity: NonNegativeDecimal
    unfilled_quantity: NonNegativeDecimal
    fill_price: NonNegativeDecimal
    used_market_event_ids: list[str] = Field(default_factory=list)
    slippage: Decimal
    fill_model_version: str
    filled_at_utc: EpochNanos
    status: OrderStatus


class AccountSnapshot(BaseModel):
    """BrokerProvider 계좌 상태. v1은 PaperBrokerProvider만 이 타입을 생성한다."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    as_of_utc: EpochNanos
    cash_by_currency: dict[Currency, Decimal] = Field(default_factory=dict)
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    positions: dict[str, Decimal] = Field(default_factory=dict)  # instrument_id -> 수량(음수=숏)
