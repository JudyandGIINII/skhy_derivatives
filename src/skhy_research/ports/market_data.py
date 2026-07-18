"""실시간 quote/trade/예상체결 구독 포트 (PRD 7.4 MarketDataProvider)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from skhy_research.domain.market import MarketQuote, Trade
from skhy_research.domain.provider_capability import ConnectionHealth, ProviderCatalogEntry


class MarketDataProvider(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def connection_health(self) -> ConnectionHealth: ...

    def subscribe_quotes(self, instrument_ids: Sequence[str]) -> AsyncIterator[MarketQuote]: ...

    def subscribe_trades(self, instrument_ids: Sequence[str]) -> AsyncIterator[Trade]: ...

    async def unsubscribe(self, instrument_ids: Sequence[str]) -> None: ...
