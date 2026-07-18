"""내부 안정 instrument_id, 원천 symbol alias, 기업행사 버전 (PRD 6장, FR-04).

종목 목록은 정적으로 고정하지 않는다. `SymbolAlias`는 (source, venue, symbol)이
가리키는 `instrument_id`를 유효기간과 함께 기록해 심볼 변경·상장폐지를
추적하고, `CorporateActionRecord`는 조정 계수를 버전으로 관리해 원시가격을
덮어쓰지 않는다.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

from skhy_research.domain.enums import AssetClass, Venue
from skhy_research.domain.market import EpochNanos


class InstrumentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_id: str
    asset_class: AssetClass
    primary_venue: Venue
    display_name: str
    is_active: bool
    listed_at_utc: EpochNanos | None = None
    delisted_at_utc: EpochNanos | None = None

    @model_validator(mode="after")
    def _delisted_after_listed(self) -> InstrumentRecord:
        if (
            self.listed_at_utc is not None
            and self.delisted_at_utc is not None
            and self.delisted_at_utc <= self.listed_at_utc
        ):
            raise ValueError("delisted_at_utc는 listed_at_utc보다 이후여야 한다")
        return self


class SymbolAlias(BaseModel):
    """(source, venue, symbol) -> instrument_id, 유효구간 포함. `effective_to_utc=None`은 현재까지 유효."""

    model_config = ConfigDict(frozen=True)

    instrument_id: str
    source: str
    venue: Venue
    symbol: str
    effective_from_utc: EpochNanos
    effective_to_utc: EpochNanos | None = None

    @model_validator(mode="after")
    def _effective_to_after_from(self) -> SymbolAlias:
        if self.effective_to_utc is not None and self.effective_to_utc <= self.effective_from_utc:
            raise ValueError("effective_to_utc는 effective_from_utc보다 이후여야 한다")
        return self

    def covers(self, as_of_utc: int) -> bool:
        if as_of_utc < self.effective_from_utc:
            return False
        return self.effective_to_utc is None or as_of_utc < self.effective_to_utc


class CorporateActionRecord(BaseModel):
    """기업행사 조정 계수의 버전. 원시가격은 수정하지 않고 이 계수로 정규화 계층에서 조정한다."""

    model_config = ConfigDict(frozen=True)

    instrument_id: str
    action_type: str  # SPLIT|DIVIDEND|MERGER|SYMBOL_CHANGE 등
    effective_date_utc: EpochNanos
    adjustment_factor: Decimal
    version: int
    announced_at_utc: EpochNanos
    source_url: str | None = None

    @model_validator(mode="after")
    def _adjustment_factor_positive(self) -> CorporateActionRecord:
        if self.adjustment_factor <= 0:
            raise ValueError("adjustment_factor는 양수여야 한다")
        return self
