"""전략 신호 타입 (PRD 8.2 Signal)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skhy_research.domain.enums import SignalDirection
from skhy_research.domain.market import EpochNanos


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    direction: SignalDirection
    confidence: Decimal  # 0..1
    expected_gross_return: Decimal
    expected_cost: Decimal
    expected_net_return: Decimal
    generated_at_utc: EpochNanos
    expires_at_utc: EpochNanos
    feature_set_id: str
    input_record_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _confidence_in_unit_interval(self) -> Signal:
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise ValueError("confidence는 0..1 범위여야 한다")
        return self

    @model_validator(mode="after")
    def _net_return_is_gross_minus_cost(self) -> Signal:
        if self.expected_net_return != self.expected_gross_return - self.expected_cost:
            raise ValueError("expected_net_return은 expected_gross_return - expected_cost여야 한다")
        return self

    @model_validator(mode="after")
    def _expiry_after_generation(self) -> Signal:
        if self.expires_at_utc <= self.generated_at_utc:
            raise ValueError("expires_at_utc는 generated_at_utc보다 이후여야 한다")
        return self
