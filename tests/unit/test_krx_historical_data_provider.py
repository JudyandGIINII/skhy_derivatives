"""KRX 날짜 응답 공유 cache·pacing·rate-limit 재시도 계약."""

from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path
from typing import Any

from skhy_research.adapters.providers.krx.historical_data_provider import (
    KrxHistoricalDataProvider,
)
from skhy_research.domain.calendar import local_datetime_to_utc_nanos
from skhy_research.domain.enums import AdjustmentStatus, Venue
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)
from skhy_research.ports.errors import ProviderRateLimitError

_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "krx" / "stk_bydd_trd_multi_symbol_20260717.json"
)


def _records(basis_date: date) -> list[dict[str, Any]]:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = payload["OutBlock_1"]
    for row in records:
        row["BAS_DD"] = basis_date.strftime("%Y%m%d")
    return records


def _catalog() -> ProviderCatalogEntry:
    return ProviderCatalogEntry(
        provider_name="krx",
        port_type="historical_data",
        catalog_version="krx-test-v1",
        capabilities=frozenset({ProviderCapability.HISTORICAL_BARS}),
        license_terms_url="https://example.test/krx-terms",
        storage_redistribution_allowed=False,
        last_verified_at_utc=0,
        health_status=HealthStatus.HEALTHY,
    )


class _FixtureClient:
    def __init__(self, records_by_date: dict[date, list[dict[str, Any]]]) -> None:
        self.records_by_date = records_by_date
        self.calls: list[date] = []

    def capabilities(self) -> ProviderCatalogEntry:
        return _catalog()

    def fetch_daily_stock_trades(self, trading_date: date) -> list[dict[str, Any]]:
        self.calls.append(trading_date)
        return self.records_by_date.get(trading_date, [])


def _range(basis_date: date) -> tuple[int, int]:
    return (
        local_datetime_to_utc_nanos(basis_date, time(0, 0), Venue.KRX),
        local_datetime_to_utc_nanos(basis_date, time(23, 59), Venue.KRX),
    )


def test_two_symbols_share_one_full_market_date_response() -> None:
    basis_date = date(2026, 7, 17)
    client = _FixtureClient({basis_date: _records(basis_date)})
    provider = KrxHistoricalDataProvider(client, min_request_interval_seconds=0)
    start_utc, end_utc = _range(basis_date)

    skhy = provider.get_bars(
        "KRX_000660_COMMON_STOCK", "1d", start_utc, end_utc, AdjustmentStatus.RAW
    )
    samsung = provider.get_bars(
        "KRX_005930_COMMON_STOCK", "1d", start_utc, end_utc, AdjustmentStatus.RAW
    )

    assert client.calls == [basis_date]
    assert skhy[0].symbol == "000660"
    assert skhy[0].close == 500000
    assert samsung[0].symbol == "005930"
    assert samsung[0].close == 86500


def test_prefetch_skips_weekend_and_marks_empty_weekday_as_non_trading() -> None:
    friday = date(2026, 7, 17)
    monday = date(2026, 7, 20)
    tuesday = date(2026, 7, 21)
    client = _FixtureClient({friday: _records(friday), tuesday: _records(tuesday)})
    provider = KrxHistoricalDataProvider(client, min_request_interval_seconds=0)

    result = provider.prefetch_latest_trading_days(end=tuesday, minimum_trading_days=2)

    assert result.trading_dates == (friday, tuesday)
    assert result.non_trading_weekdays == (monday,)
    assert client.calls == [tuesday, monday, friday]


def test_rate_limit_error_waits_and_retries_same_date() -> None:
    basis_date = date(2026, 7, 17)

    class _RateLimitedClient(_FixtureClient):
        def fetch_daily_stock_trades(self, trading_date: date) -> list[dict[str, Any]]:
            self.calls.append(trading_date)
            if len(self.calls) == 1:
                raise ProviderRateLimitError("krx", retry_after_seconds=2.5)
            return _records(trading_date)

    client = _RateLimitedClient({})
    waits: list[float] = []
    provider = KrxHistoricalDataProvider(
        client,
        min_request_interval_seconds=0,
        max_rate_limit_retries=1,
        sleep=waits.append,
    )

    result = provider.prefetch_latest_trading_days(end=basis_date, minimum_trading_days=1)

    assert result.trading_dates == (basis_date,)
    assert client.calls == [basis_date, basis_date]
    assert waits == [2.5]
