"""sanitized KIS/Toss 응답의 point-in-time snapshot 매핑 검증."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from skhy_research.adapters.providers.kis.market_data import KisSnapshotMarketDataProvider
from skhy_research.adapters.providers.toss.market_data import TossSnapshotMarketDataProvider
from skhy_research.domain.enums import AssetClass, MarketDataFeedMode
from skhy_research.domain.market import IndicativeValueKind, ObservationTimeSource
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)
from skhy_research.ports.market_data import MarketSnapshotTarget
from skhy_research.strategies.h1_close_rebalance.decision_window import build_decision_window

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"


def _load(provider: str) -> dict[str, Any]:
    return json.loads(
        (_FIXTURE_ROOT / provider / "h1_live_snapshot_sanitized.json").read_text(
            encoding="utf-8"
        )
    )


def _catalog(provider: str) -> ProviderCatalogEntry:
    return ProviderCatalogEntry(
        provider_name=provider,
        port_type="market_data",
        catalog_version=f"{provider}-fixture-v1",
        capabilities=frozenset({ProviderCapability.QUOTE_SNAPSHOT}),
        license_terms_url="https://example.test/terms",
        storage_redistribution_allowed=False,
        last_verified_at_utc=0,
        health_status=HealthStatus.HEALTHY,
    )


class _KisFixtureClient:
    def __init__(self, payload: dict[str, Any], environment: Literal["vps", "prod"]) -> None:
        self.payload = payload
        self.environment: Literal["vps", "prod"] = environment

    def capabilities(self) -> ProviderCatalogEntry:
        return _catalog("kis")

    def fetch_domestic_quote(self, symbol: str = "000660", market: str = "J") -> dict[str, Any]:
        assert market == "J"
        return self.payload["domestic_quote"][symbol]

    def fetch_intraday_prices(
        self, symbol: str, *, as_of_time_kst: str, market: str = "J"
    ) -> list[dict[str, Any]]:
        assert as_of_time_kst == "151000"
        assert market == "J"
        return self.payload["intraday_prices"][symbol]

    def fetch_etf_etn_quote(self, symbol: str, market: str = "J") -> dict[str, Any]:
        assert market == "J"
        return self.payload["etf_etn_quote"][symbol]

    def fetch_etf_nav_intraday(
        self, symbol: str, *, interval_seconds: str = "60"
    ) -> list[dict[str, Any]]:
        assert interval_seconds == "60"
        return self.payload["nav_intraday"][symbol]


class _TossFixtureClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def capabilities(self) -> ProviderCatalogEntry:
        return _catalog("toss")

    def fetch_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        assert symbols == ["000660", "0193T0"]
        return self.payload["prices"]

    def fetch_orderbook(self, symbol: str) -> dict[str, Any]:
        assert symbol == "000660"
        return self.payload["orderbook"]


def _clock(values: Iterator[int]):
    return lambda: next(values)


def _targets() -> list[MarketSnapshotTarget]:
    return [
        MarketSnapshotTarget("KRX_000660_COMMON_STOCK", "000660", AssetClass.COMMON_STOCK),
        MarketSnapshotTarget("KRX_0193T0_LEVERAGED_ETF", "0193T0", AssetClass.LEVERAGED_ETF),
    ]


def test_kis_combines_provider_date_time_and_minute_nav_without_fabricated_quote() -> None:
    window = build_decision_window(date(2026, 7, 16), "15:10:00", "15:19:30")
    start = window.signal_snapshot_utc
    client = _KisFixtureClient(_load("kis"), "prod")
    provider = KisSnapshotMarketDataProvider(
        client,
        clock_ns=_clock(iter((start + 1_000_000_000, start + 2_000_000_000, start + 3_000_000_000))),
    )

    batch = provider.get_price_snapshots(_targets(), requested_as_of_utc=start)
    snapshots = {item.instrument_id: item for item in batch.snapshots}
    underlying = snapshots["KRX_000660_COMMON_STOCK"]
    etf = snapshots["KRX_0193T0_LEVERAGED_ETF"]

    assert underlying.last_price == Decimal("1836000")
    assert underlying.event_time_utc == start
    assert underlying.observation_time_source is ObservationTimeSource.PROVIDER_DATE_TIME
    assert underlying.feed_mode is MarketDataFeedMode.LIVE
    assert etf.last_price == Decimal("14585")
    assert etf.indicative_value == Decimal("14468.37")
    assert etf.indicative_value_kind is IndicativeValueKind.NAV
    assert etf.indicative_value_observed_at_utc == start
    assert batch.received_at_utc == start + 3_000_000_000
    assert provider.connection_health().is_connected is True


def test_kis_vps_is_explicitly_simulated() -> None:
    window = build_decision_window(date(2026, 7, 16), "15:10:00", "15:19:30")
    start = window.signal_snapshot_utc
    provider = KisSnapshotMarketDataProvider(
        _KisFixtureClient(_load("kis"), "vps"),
        clock_ns=_clock(iter((start + 1_000_000_000, start + 2_000_000_000, start + 3_000_000_000))),
    )

    batch = provider.get_price_snapshots(_targets(), requested_as_of_utc=start)

    assert all(item.feed_mode is MarketDataFeedMode.SIMULATED for item in batch.snapshots)
    assert all(item.is_delayed for item in batch.snapshots)


def test_toss_maps_provider_timestamp_and_real_orderbook_to_distinct_domain_types() -> None:
    window = build_decision_window(date(2026, 7, 16), "15:10:00", "15:19:30")
    start = window.signal_snapshot_utc
    provider = TossSnapshotMarketDataProvider(
        _TossFixtureClient(_load("toss")),
        clock_ns=_clock(iter((start + 1_000_000_000, start + 2_000_000_000, start + 7_000_000_000))),
    )

    batch = provider.get_price_snapshots(_targets(), requested_as_of_utc=start)
    snapshots = {item.instrument_id: item for item in batch.snapshots}
    quote = provider.get_orderbook_quote(_targets()[0])

    assert snapshots["KRX_000660_COMMON_STOCK"].last_price == Decimal("1835000")
    assert snapshots["KRX_000660_COMMON_STOCK"].observation_time_source is (
        ObservationTimeSource.PROVIDER_TIMESTAMP
    )
    assert quote.bid_price == Decimal("1836000")
    assert quote.ask_price == Decimal("1837000")
    assert quote.bid_size == Decimal("323")
    assert quote.ask_size == Decimal("910")
