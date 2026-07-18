"""Toss timestamp 현재가·호가를 도메인 snapshot/quote로 매핑한다."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from skhy_research.adapters.providers.snapshot_support import (
    parse_provider_iso_timestamp,
    session_at,
)
from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    MarketDataFeedMode,
    Venue,
)
from skhy_research.domain.market import (
    MarketPriceSnapshot,
    MarketQuote,
    ObservationTimeSource,
    PublicationTimeSource,
)
from skhy_research.domain.provider_capability import ConnectionHealth, ProviderCatalogEntry
from skhy_research.ports.market_data import MarketSnapshotBatch, MarketSnapshotTarget


class TossSnapshotMappingError(ValueError):
    """Toss raw payload를 시각 안전한 도메인 레코드로 매핑할 수 없을 때."""


class _TossSnapshotClient(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def fetch_prices(self, symbols: list[str]) -> list[dict[str, Any]]: ...

    def fetch_orderbook(self, symbol: str) -> dict[str, Any]: ...


class TossSnapshotMarketDataProvider:
    def __init__(
        self,
        client: _TossSnapshotClient,
        *,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._client = client
        self._clock_ns = clock_ns
        self._last_received_at_utc: int | None = None
        self._measured_latency_ms: float | None = None

    def capabilities(self) -> ProviderCatalogEntry:
        return self._client.capabilities()

    def connection_health(self) -> ConnectionHealth:
        return ConnectionHealth(
            is_connected=self._last_received_at_utc is not None,
            measured_latency_ms=self._measured_latency_ms,
            last_event_at_utc=self._last_received_at_utc,
        )

    def get_price_snapshots(
        self,
        targets: Sequence[MarketSnapshotTarget],
        *,
        requested_as_of_utc: int,
    ) -> MarketSnapshotBatch:
        if not targets:
            raise ValueError("snapshot targets는 비어 있을 수 없다")
        mapping = {item.symbol: item for item in targets}
        if len(mapping) != len(targets):
            raise ValueError("Toss snapshot symbol이 중복됐다")
        if len({item.instrument_id for item in targets}) != len(targets):
            raise ValueError("Toss snapshot instrument_id가 중복됐다")

        started_at = self._clock_ns()
        rows = self._client.fetch_prices(list(mapping))
        received_at = self._clock_ns()
        snapshots: list[MarketPriceSnapshot] = []
        seen: set[str] = set()
        for row in rows:
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or symbol not in mapping:
                raise TossSnapshotMappingError("요청하지 않은 Toss symbol이 응답에 있다")
            if symbol in seen:
                raise TossSnapshotMappingError(f"Toss symbol 중복: {symbol}")
            seen.add(symbol)
            event_time = parse_provider_iso_timestamp(row.get("timestamp"))
            if event_time > received_at:
                raise TossSnapshotMappingError("Toss 공급자 timestamp가 수신시각보다 늦다")
            currency = _currency(row.get("currency"))
            target = mapping[symbol]
            snapshots.append(
                MarketPriceSnapshot(
                    record_id=f"toss:prod:{symbol}:{event_time}",
                    source="TOSS_PROD_REST",
                    venue=Venue.KRX,
                    symbol=symbol,
                    event_time_utc=event_time,
                    received_time_utc=received_at,
                    currency=currency,
                    session=session_at(event_time, Venue.KRX),
                    is_delayed=False,
                    adjustment_status=AdjustmentStatus.RAW,
                    instrument_id=target.instrument_id,
                    last_price=_positive_decimal(row.get("lastPrice"), field="lastPrice"),
                    published_time_utc=received_at,
                    observation_time_source=ObservationTimeSource.PROVIDER_TIMESTAMP,
                    publication_time_source=PublicationTimeSource.CLIENT_RECEIVED_AT,
                    feed_mode=MarketDataFeedMode.LIVE,
                )
            )
        missing = set(mapping) - seen
        if missing:
            raise TossSnapshotMappingError(f"Toss 현재가 응답 누락: {sorted(missing)}")

        ordered = tuple(sorted(snapshots, key=lambda item: item.instrument_id))
        self._last_received_at_utc = received_at
        self._measured_latency_ms = (received_at - started_at) / 1_000_000
        return MarketSnapshotBatch("toss", requested_as_of_utc, received_at, ordered)

    def get_orderbook_quote(self, target: MarketSnapshotTarget) -> MarketQuote:
        """Toss가 제공하는 양방향 호가만 `MarketQuote`로 매핑한다."""

        row = self._client.fetch_orderbook(target.symbol)
        received_at = self._clock_ns()
        event_time = parse_provider_iso_timestamp(row.get("timestamp"))
        if event_time > received_at:
            raise TossSnapshotMappingError("Toss 호가 timestamp가 수신시각보다 늦다")
        asks = _levels(row.get("asks"), side="asks")
        bids = _levels(row.get("bids"), side="bids")
        ask = asks[0]
        bid = bids[0]
        self._last_received_at_utc = received_at
        return MarketQuote(
            source="TOSS_PROD_REST",
            venue=Venue.KRX,
            symbol=target.symbol,
            event_time_utc=event_time,
            received_time_utc=received_at,
            currency=_currency(row.get("currency")),
            session=session_at(event_time, Venue.KRX),
            is_delayed=False,
            adjustment_status=AdjustmentStatus.RAW,
            instrument_id=target.instrument_id,
            bid_price=bid[0],
            ask_price=ask[0],
            bid_size=bid[1],
            ask_size=ask[1],
        )


def _levels(value: object, *, side: str) -> list[tuple[Decimal, Decimal]]:
    if not isinstance(value, list) or not value:
        raise TossSnapshotMappingError(f"Toss {side}가 없다")
    levels: list[tuple[Decimal, Decimal]] = []
    for row in value:
        if not isinstance(row, dict):
            raise TossSnapshotMappingError(f"Toss {side} level이 object가 아니다")
        levels.append(
            (
                _positive_decimal(row.get("price"), field=f"{side}.price"),
                _positive_decimal(row.get("volume"), field=f"{side}.volume"),
            )
        )
    return levels


def _currency(value: object) -> Currency:
    if value != "KRW":
        raise TossSnapshotMappingError(f"H1 국내 종목의 Toss currency가 KRW가 아님: {value}")
    return Currency.KRW


def _positive_decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise TossSnapshotMappingError(f"Toss {field}를 Decimal로 파싱할 수 없다") from exc
    if parsed <= 0:
        raise TossSnapshotMappingError(f"Toss {field}는 0보다 커야 한다")
    return parsed
