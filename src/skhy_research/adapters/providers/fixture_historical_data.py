"""sanitized fixture 기반 HistoricalDataProvider 구현 (P0-07)."""

from __future__ import annotations

from skhy_research.adapters.providers.fixture_support import FixtureCallGateway, FixtureScenario
from skhy_research.domain.enums import AdjustmentStatus
from skhy_research.domain.market import Bar
from skhy_research.domain.provider_capability import ProviderCapability, ProviderCatalogEntry
from skhy_research.ports.errors import UnsupportedCapabilityError


class FixtureHistoricalDataProvider:
    def __init__(
        self,
        catalog_entry: ProviderCatalogEntry,
        gateway: FixtureCallGateway,
        bars_scenario: FixtureScenario | None = None,
    ) -> None:
        self._entry = catalog_entry
        self._gateway = gateway
        self._bars_scenario = bars_scenario

    def capabilities(self) -> ProviderCatalogEntry:
        return self._entry

    def get_bars(
        self,
        instrument_id: str,
        period: str,
        start_utc: int,
        end_utc: int,
        adjustment: AdjustmentStatus,
    ) -> list[Bar]:
        if not self._entry.supports(ProviderCapability.HISTORICAL_BARS):
            raise UnsupportedCapabilityError(self._entry.provider_name, ProviderCapability.HISTORICAL_BARS)
        if self._bars_scenario is None:
            return []
        payload = self._gateway.resolve(self._bars_scenario)
        return [Bar(**row) for row in (payload or [])]
