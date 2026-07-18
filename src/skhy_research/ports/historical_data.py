"""기간·주기·조정방식이 명시된 bar/통계 백필 포트 (PRD 7.4 HistoricalDataProvider)."""

from __future__ import annotations

from typing import Protocol

from skhy_research.domain.enums import AdjustmentStatus
from skhy_research.domain.market import Bar
from skhy_research.domain.provider_capability import ProviderCatalogEntry


class HistoricalDataProvider(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def get_bars(
        self,
        instrument_id: str,
        period: str,
        start_utc: int,
        end_utc: int,
        adjustment: AdjustmentStatus,
    ) -> list[Bar]:
        """구간별 source segment(lineage)는 반환된 각 Bar.source/quality_flag에 남는다."""
        ...
