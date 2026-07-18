"""리스크 판정 타입 (PRD 8.2 RiskDecision)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skhy_research.domain.enums import RiskDecisionType
from skhy_research.domain.market import EpochNanos, NonNegativeDecimal


class RiskDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str
    order_intent_id: str | None  # 수량 산정 이전의 사전 게이트일 수 있어 nullable
    decision: RiskDecisionType
    reason_codes: list[str] = Field(default_factory=list)
    requested_quantity: NonNegativeDecimal
    approved_quantity: NonNegativeDecimal
    limits_snapshot: dict[str, Decimal] = Field(default_factory=dict)
    decided_at_utc: EpochNanos

    @model_validator(mode="after")
    def _block_means_zero_approved(self) -> RiskDecision:
        if self.decision == RiskDecisionType.BLOCK and self.approved_quantity != Decimal("0"):
            raise ValueError("BLOCK 판정은 approved_quantity=0이어야 한다")
        return self

    @model_validator(mode="after")
    def _reduce_or_block_needs_reason(self) -> RiskDecision:
        if self.decision in (RiskDecisionType.BLOCK, RiskDecisionType.REDUCE) and not self.reason_codes:
            raise ValueError("BLOCK/REDUCE 판정에는 최소 1개의 사유코드가 필요하다")
        return self

    @model_validator(mode="after")
    def _approved_not_exceed_requested(self) -> RiskDecision:
        if self.approved_quantity > self.requested_quantity:
            raise ValueError("approved_quantity는 requested_quantity를 초과할 수 없다")
        return self
