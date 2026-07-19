"""sanitized fixture 기반 MarketDataProvider 구현 (P0-07).

오류 시나리오(인증 실패·rate limit·timeout)는 구독 호출 시점에 동기적으로
평가한다. 실제 어댑터도 구독 수립 단계에서 동일하게 동작해야 한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from skhy_research.adapters.providers.fixture_support import FixtureCallGateway, FixtureScenario
from skhy_research.domain.market import MarketQuote, Trade
from skhy_research.domain.provider_capability import (
    ConnectionHealth,
    ProviderCapability,
    ProviderCatalogEntry,
)
from skhy_research.ports.errors import UnsupportedCapabilityError


class FixtureMarketDataProvider:
    def __init__(
        self,
        catalog_entry: ProviderCatalogEntry,
        gateway: FixtureCallGateway,
        connection_health: ConnectionHealth,
        quotes_scenario: FixtureScenario | None = None,
        trades_scenario: FixtureScenario | None = None,
    ) -> None:
        self._entry = catalog_entry
        self._gateway = gateway
        self._connection_health = connection_health
        self._quotes_scenario = quotes_scenario
        self._trades_scenario = trades_scenario

    def capabilities(self) -> ProviderCatalogEntry:
        return self._entry

    def connection_health(self) -> ConnectionHealth:
        return self._connection_health

    def subscribe_quotes(self, instrument_ids: Sequence[str]) -> AsyncIterator[MarketQuote]:
        if not self._entry.supports(ProviderCapability.QUOTE_STREAM):
            raise UnsupportedCapabilityError(self._entry.provider_name, ProviderCapability.QUOTE_STREAM)
        payload = self._gateway.resolve(self._quotes_scenario) if self._quotes_scenario else []

        async def _iterate() -> AsyncIterator[MarketQuote]:
            for row in payload or []:
                yield MarketQuote(**row)

        return _iterate()

    def subscribe_trades(self, instrument_ids: Sequence[str]) -> AsyncIterator[Trade]:
        if not self._entry.supports(ProviderCapability.TRADE_STREAM):
            raise UnsupportedCapabilityError(self._entry.provider_name, ProviderCapability.TRADE_STREAM)
        payload = self._gateway.resolve(self._trades_scenario) if self._trades_scenario else []

        async def _iterate() -> AsyncIterator[Trade]:
            for row in payload or []:
                yield Trade(**row)

        return _iterate()

    async def unsubscribe(self, instrument_ids: Sequence[str]) -> None:
        return None
