"""KIS REST 현재가·분봉·NAV를 결합한 H1 point-in-time snapshot provider."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from skhy_research.adapters.providers.kis.client import KisEnvironment
from skhy_research.adapters.providers.snapshot_support import (
    combine_kis_time,
    parse_kis_date_time,
    provider_trading_date,
    requested_time_kst,
    session_at,
)
from skhy_research.domain.enums import (
    AdjustmentStatus,
    AssetClass,
    Currency,
    MarketDataFeedMode,
    QualityFlag,
    Venue,
)
from skhy_research.domain.market import (
    IndicativeValueKind,
    MarketPriceSnapshot,
    ObservationTimeSource,
    PublicationTimeSource,
)
from skhy_research.domain.provider_capability import ConnectionHealth, ProviderCatalogEntry
from skhy_research.ports.market_data import (
    MarketSnapshotBatch,
    MarketSnapshotTarget,
)


class KisSnapshotMappingError(ValueError):
    """KIS raw 시각·가격을 lookahead-safe snapshot으로 매핑할 수 없을 때."""


class _KisSnapshotClient(Protocol):
    @property
    def environment(self) -> KisEnvironment: ...

    def capabilities(self) -> ProviderCatalogEntry: ...

    def fetch_domestic_quote(self, symbol: str = "000660", market: str = "J") -> dict[str, Any]: ...

    def fetch_intraday_prices(
        self, symbol: str, *, as_of_time_kst: str, market: str = "J"
    ) -> list[dict[str, Any]]: ...

    def fetch_etf_etn_quote(self, symbol: str, market: str = "J") -> dict[str, Any]: ...

    def fetch_etf_nav_intraday(
        self, symbol: str, *, interval_seconds: str = "60"
    ) -> list[dict[str, Any]]: ...


class KisSnapshotMarketDataProvider:
    """KIS prod만 LIVE로 표시하고 vps는 SIMULATED로 강제 분리한다."""

    def __init__(
        self,
        client: _KisSnapshotClient,
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
        _validate_targets(targets)
        started_at_utc = self._clock_ns()
        as_of_kst = requested_time_kst(requested_as_of_utc)
        common_targets = [item for item in targets if item.asset_class is AssetClass.COMMON_STOCK]
        etp_targets = [item for item in targets if item.asset_class is not AssetClass.COMMON_STOCK]
        if etp_targets and not common_targets:
            raise KisSnapshotMappingError(
                "ETF/ETN bsop_hour의 거래일을 확정할 KIS 기초주식 분봉이 필요하다"
            )

        snapshots: list[MarketPriceSnapshot] = []
        batch_dates: set[date] = set()
        for target in common_targets:
            current = self._client.fetch_domestic_quote(target.symbol)
            _positive_decimal(current.get("stck_prpr"), field="stck_prpr")
            rows = self._client.fetch_intraday_prices(
                target.symbol,
                as_of_time_kst=as_of_kst,
            )
            row, event_time = _select_stock_row(rows, requested_as_of_utc)
            batch_dates.add(provider_trading_date(event_time))
            received_at = self._clock_ns()
            snapshots.append(
                self._stock_snapshot(target, row, event_time, received_at)
            )

        if len(batch_dates) > 1:
            raise KisSnapshotMappingError(f"KIS 기초주식의 거래일이 다르다: {sorted(batch_dates)}")
        batch_date = next(iter(batch_dates)) if batch_dates else None

        for target in etp_targets:
            assert batch_date is not None
            current = self._client.fetch_domestic_quote(target.symbol)
            _positive_decimal(current.get("stck_prpr"), field="stck_prpr")
            rows = self._client.fetch_etf_nav_intraday(target.symbol)
            row, event_time = _select_nav_row(rows, batch_date, requested_as_of_utc)
            received_at = self._clock_ns()
            snapshots.append(
                self._etp_snapshot(target, row, event_time, received_at)
            )

        ordered = tuple(sorted(snapshots, key=lambda item: item.instrument_id))
        received_at_utc = max(item.received_time_utc for item in ordered)
        self._last_received_at_utc = received_at_utc
        self._measured_latency_ms = (received_at_utc - started_at_utc) / 1_000_000
        return MarketSnapshotBatch(
            provider_name="kis",
            requested_as_of_utc=requested_as_of_utc,
            received_at_utc=received_at_utc,
            snapshots=ordered,
        )

    def _stock_snapshot(
        self,
        target: MarketSnapshotTarget,
        row: dict[str, Any],
        event_time: int,
        received_at: int,
    ) -> MarketPriceSnapshot:
        return self._snapshot(
            target,
            event_time=event_time,
            received_at=received_at,
            last_price=_positive_decimal(row.get("stck_prpr"), field="stck_prpr"),
            indicative_value=None,
        )

    def _etp_snapshot(
        self,
        target: MarketSnapshotTarget,
        row: dict[str, Any],
        event_time: int,
        received_at: int,
    ) -> MarketPriceSnapshot:
        nav = _optional_positive_decimal(row.get("nav"), field="nav")
        return self._snapshot(
            target,
            event_time=event_time,
            received_at=received_at,
            last_price=_positive_decimal(row.get("stck_prpr"), field="stck_prpr"),
            indicative_value=nav,
        )

    def _snapshot(
        self,
        target: MarketSnapshotTarget,
        *,
        event_time: int,
        received_at: int,
        last_price: Decimal,
        indicative_value: Decimal | None,
    ) -> MarketPriceSnapshot:
        if received_at < event_time:
            raise KisSnapshotMappingError("KIS 수신시각이 공급자 관측시각보다 이르다")
        is_live = self._client.environment == "prod"
        quality = [] if is_live else [QualityFlag.DELAYED]
        return MarketPriceSnapshot(
            record_id=f"kis:{self._client.environment}:{target.symbol}:{event_time}",
            source=f"KIS_{self._client.environment.upper()}_REST",
            venue=Venue.KRX,
            symbol=target.symbol,
            event_time_utc=event_time,
            received_time_utc=received_at,
            currency=Currency.KRW,
            session=session_at(event_time, Venue.KRX),
            is_delayed=not is_live,
            adjustment_status=AdjustmentStatus.RAW,
            quality_flag=quality,
            instrument_id=target.instrument_id,
            last_price=last_price,
            published_time_utc=received_at,
            observation_time_source=(
                ObservationTimeSource.PROVIDER_DATE_TIME
                if target.asset_class is AssetClass.COMMON_STOCK
                else ObservationTimeSource.PROVIDER_TIME_WITH_BATCH_DATE
            ),
            publication_time_source=PublicationTimeSource.CLIENT_RECEIVED_AT,
            feed_mode=(MarketDataFeedMode.LIVE if is_live else MarketDataFeedMode.SIMULATED),
            indicative_value=indicative_value,
            indicative_value_kind=(IndicativeValueKind.NAV if indicative_value is not None else None),
            indicative_value_observed_at_utc=(event_time if indicative_value is not None else None),
        )


def _select_stock_row(
    rows: list[dict[str, Any]], requested_as_of_utc: int
) -> tuple[dict[str, Any], int]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        try:
            event_time = parse_kis_date_time(
                row.get("stck_bsop_date"),
                row.get("stck_cntg_hour"),
            )
        except ValueError:
            continue
        if event_time <= requested_as_of_utc:
            candidates.append((event_time, row))
    if not candidates:
        raise KisSnapshotMappingError("requested_as_of 이전 KIS 분봉이 없다")
    event_time, row = max(candidates, key=lambda item: item[0])
    return row, event_time


def _select_nav_row(
    rows: list[dict[str, Any]],
    batch_date: date,
    requested_as_of_utc: int,
) -> tuple[dict[str, Any], int]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        try:
            event_time = combine_kis_time(batch_date, row.get("bsop_hour"))
        except ValueError:
            continue
        if event_time <= requested_as_of_utc:
            candidates.append((event_time, row))
    if not candidates:
        raise KisSnapshotMappingError("requested_as_of 이전 KIS NAV 분별 레코드가 없다")
    event_time, row = max(candidates, key=lambda item: item[0])
    return row, event_time


def _positive_decimal(value: object, *, field: str) -> Decimal:
    parsed = _optional_positive_decimal(value, field=field)
    if parsed is None:
        raise KisSnapshotMappingError(f"KIS {field}가 없거나 0 이하다")
    return parsed


def _optional_positive_decimal(value: object, *, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise KisSnapshotMappingError(f"KIS {field}를 Decimal로 파싱할 수 없다") from exc
    if parsed <= 0:
        return None
    return parsed


def _validate_targets(targets: Sequence[MarketSnapshotTarget]) -> None:
    if not targets:
        raise ValueError("snapshot targets는 비어 있을 수 없다")
    instrument_ids = [item.instrument_id for item in targets]
    symbols = [item.symbol for item in targets]
    if len(set(instrument_ids)) != len(instrument_ids):
        raise ValueError("snapshot instrument_id가 중복됐다")
    if len(set(symbols)) != len(symbols):
        raise ValueError("snapshot symbol이 중복됐다")
