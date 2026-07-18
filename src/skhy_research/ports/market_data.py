"""실시간 quote/trade/예상체결 구독 포트 (PRD 7.4 MarketDataProvider)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from skhy_research.domain.enums import AssetClass
from skhy_research.domain.market import MarketPriceSnapshot, MarketQuote, Trade
from skhy_research.domain.provider_capability import ConnectionHealth, ProviderCatalogEntry


class MarketDataProvider(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def connection_health(self) -> ConnectionHealth: ...

    def subscribe_quotes(self, instrument_ids: Sequence[str]) -> AsyncIterator[MarketQuote]: ...

    def subscribe_trades(self, instrument_ids: Sequence[str]) -> AsyncIterator[Trade]: ...

    async def unsubscribe(self, instrument_ids: Sequence[str]) -> None: ...


@dataclass(frozen=True)
class MarketSnapshotTarget:
    instrument_id: str
    symbol: str
    asset_class: AssetClass


@dataclass(frozen=True)
class MarketSnapshotBatch:
    provider_name: str
    requested_as_of_utc: int
    received_at_utc: int
    snapshots: tuple[MarketPriceSnapshot, ...]


class MarketDataSnapshotProvider(Protocol):
    """Phase 1의 15:10 point-in-time REST snapshot 계약.

    연속 websocket 구독·재연결·스케줄링은 Phase 2 범위다.
    """

    def capabilities(self) -> ProviderCatalogEntry: ...

    def connection_health(self) -> ConnectionHealth: ...

    def get_price_snapshots(
        self,
        targets: Sequence[MarketSnapshotTarget],
        *,
        requested_as_of_utc: int,
    ) -> MarketSnapshotBatch: ...
