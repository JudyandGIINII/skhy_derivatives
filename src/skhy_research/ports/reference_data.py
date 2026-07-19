"""종목 마스터·기업행사·상품 구조·전환 상태 조회 포트 (PRD 7.4 ReferenceDataProvider)."""

from __future__ import annotations

from typing import Protocol

from skhy_research.domain.instrument import CorporateActionRecord, InstrumentRecord
from skhy_research.domain.provider_capability import ProviderCatalogEntry
from skhy_research.domain.reference import ConversionStatus, FundSnapshot


class ReferenceDataProvider(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def get_instrument_master(self, as_of_utc: int) -> list[InstrumentRecord]: ...

    def get_corporate_actions(
        self, instrument_id: str, as_of_utc: int
    ) -> list[CorporateActionRecord]: ...

    def get_conversion_status(self, instrument_id: str) -> ConversionStatus: ...

    def get_fund_snapshot(self, fund_id: str) -> FundSnapshot: ...
