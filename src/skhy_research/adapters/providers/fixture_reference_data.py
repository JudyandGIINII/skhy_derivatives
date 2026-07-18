"""sanitized fixture 기반 ReferenceDataProvider 구현 (P0-07)."""

from __future__ import annotations

from skhy_research.adapters.providers.fixture_support import FixtureCallGateway, FixtureScenario
from skhy_research.domain.instrument import CorporateActionRecord, InstrumentRecord
from skhy_research.domain.provider_capability import ProviderCapability, ProviderCatalogEntry
from skhy_research.domain.reference import ConversionStatus
from skhy_research.ports.errors import UnsupportedCapabilityError


class FixtureReferenceDataProvider:
    def __init__(
        self,
        catalog_entry: ProviderCatalogEntry,
        gateway: FixtureCallGateway,
        instrument_master_scenario: FixtureScenario | None = None,
        conversion_status_scenarios: dict[str, FixtureScenario] | None = None,
    ) -> None:
        self._entry = catalog_entry
        self._gateway = gateway
        self._instrument_master_scenario = instrument_master_scenario
        self._conversion_status_scenarios = conversion_status_scenarios or {}

    def capabilities(self) -> ProviderCatalogEntry:
        return self._entry

    def get_instrument_master(self, as_of_utc: int) -> list[InstrumentRecord]:
        if not self._entry.supports(ProviderCapability.INSTRUMENT_MASTER):
            raise UnsupportedCapabilityError(self._entry.provider_name, ProviderCapability.INSTRUMENT_MASTER)
        if self._instrument_master_scenario is None:
            return []
        payload = self._gateway.resolve(self._instrument_master_scenario)
        return [InstrumentRecord(**row) for row in (payload or [])]

    def get_conversion_status(self, instrument_id: str) -> ConversionStatus:
        if not self._entry.supports(ProviderCapability.ADR_RATIO_CONVERSION_STATUS):
            raise UnsupportedCapabilityError(
                self._entry.provider_name, ProviderCapability.ADR_RATIO_CONVERSION_STATUS
            )
        scenario = self._conversion_status_scenarios.get(instrument_id)
        if scenario is None:
            raise KeyError(f"{instrument_id}에 대한 fixture 시나리오가 없다")
        payload = self._gateway.resolve(scenario)
        return ConversionStatus(**payload)

    def get_corporate_actions(self, instrument_id: str, as_of_utc: int) -> list[CorporateActionRecord]:
        if not self._entry.supports(ProviderCapability.CORPORATE_ACTIONS):
            raise UnsupportedCapabilityError(self._entry.provider_name, ProviderCapability.CORPORATE_ACTIONS)
        return []
