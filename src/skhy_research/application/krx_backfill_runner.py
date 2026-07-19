"""실 KRX 일별 백필을 gate·raw·normalized·lineage·Parquet과 연결한다."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import date
from datetime import time as wall_time
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Engine

from skhy_research.adapters.calendars.static_holiday_provider import StaticHolidayProvider
from skhy_research.adapters.persistence.gate_decision_store import PostgresGateDecisionStore
from skhy_research.adapters.persistence.manifest_store import add_lineage_edge
from skhy_research.adapters.persistence.normalized_record_store import (
    save_normalized_record_idempotent,
)
from skhy_research.adapters.persistence.raw_recorder import RawRecorder
from skhy_research.adapters.providers.krx.historical_data_provider import (
    KrxHistoricalDataProvider,
)
from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.application.gate_registry_loader import load_gate_registry
from skhy_research.application.krx_backfill import (
    BackfillResult,
    assert_backfill_gates,
    backfill_daily_bars,
)
from skhy_research.application.parquet_snapshot import ParquetSnapshotWriter
from skhy_research.application.provider_registry import ProviderRegistry
from skhy_research.domain.calendar import (
    local_datetime_to_utc_nanos,
    utc_nanos_to_local_datetime,
)
from skhy_research.domain.enums import Venue
from skhy_research.domain.experiment import LineageEdge
from skhy_research.domain.market import Bar
from skhy_research.domain.provider_capability import ProviderCatalogEntry

_RAW_DATASET = "stk_bydd_trd"
_NORMALIZED_DATASET = "krx_daily_ohlcv"
_NORMALIZER_VERSION = "krx_historical_data_provider@2.0.0"


class _KrxBackfillClient(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def fetch_daily_stock_trades(self, trading_date: date) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class KrxBackfillTarget:
    instrument_id: str
    symbol: str


DEFAULT_H1_BACKFILL_TARGETS = (
    KrxBackfillTarget("KRX_000660_COMMON_STOCK", "000660"),
    KrxBackfillTarget("KRX_005930_COMMON_STOCK", "005930"),
)


@dataclass(frozen=True)
class InstrumentBackfillSummary:
    instrument_id: str
    symbol: str
    bar_count: int
    latest_bar: Bar
    result: BackfillResult


@dataclass(frozen=True)
class KrxBackfillExecutionResult:
    collection_run_id: str
    start: date
    end: date
    trading_dates: tuple[date, ...]
    requested_dates: tuple[date, ...]
    raw_inserted_count: int
    raw_duplicate_count: int
    normalized_inserted_count: int
    normalized_duplicate_count: int
    snapshot_id: str
    snapshot_path: str
    instruments: tuple[InstrumentBackfillSummary, ...]


class KrxRawPersistenceConflictError(RuntimeError):
    """같은 KRX 기준일의 기존 raw와 새 응답 checksum이 다름."""


def execute_krx_backfill(
    *,
    engine: Engine,
    data_root: Path,
    client: _KrxBackfillClient,
    end: date,
    minimum_trading_days: int,
    targets: tuple[KrxBackfillTarget, ...] = DEFAULT_H1_BACKFILL_TARGETS,
    min_request_interval_seconds: float = 0.2,
    max_rate_limit_retries: int = 4,
    max_lookback_calendar_days: int = 366,
    collection_run_id: str | None = None,
    gate_as_of_utc: int | None = None,
) -> KrxBackfillExecutionResult:
    """실제 `backfill_daily_bars` 경로를 여러 H1 종목에 공유·영속 실행한다."""

    if not targets:
        raise ValueError("KRX 백필 target은 비어 있을 수 없다")
    if len({target.instrument_id for target in targets}) != len(targets):
        raise ValueError("KRX 백필 instrument_id가 중복됐다")
    if len({target.symbol for target in targets}) != len(targets):
        raise ValueError("KRX 백필 symbol이 중복됐다")

    run_id = collection_run_id or str(uuid.uuid4())
    as_of_utc = gate_as_of_utc or time.time_ns()
    gate_registry = load_gate_registry(PostgresGateDecisionStore(engine))
    # 실 API prefetch보다 먼저 fail-closed 검사한다.
    assert_backfill_gates(gate_registry, as_of_utc)

    recorder = RawRecorder(engine, data_root)
    provider_catalog = client.capabilities()
    raw_record_ids: dict[date, str] = {}
    raw_inserted_count = 0
    raw_duplicate_count = 0

    def record_daily_raw(
        basis_date: date, records: list[dict[str, Any]], received_at_utc: int
    ) -> int:
        nonlocal raw_inserted_count, raw_duplicate_count
        payload = json.dumps(
            {"OutBlock_1": records},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        outcome = recorder.store(
            source="krx",
            dataset=_RAW_DATASET,
            payload=payload,
            received_at_utc=received_at_utc,
            collection_run_id=run_id,
            dedupe_key=f"basDd={basis_date:%Y%m%d}",
            provider_catalog=provider_catalog,
            provider_sequence=f"{basis_date:%Y%m%d}",
        )
        if outcome.was_conflict:
            raise KrxRawPersistenceConflictError(
                f"KRX {_RAW_DATASET} {basis_date:%Y-%m-%d} raw checksum 충돌"
            )
        raw_record_ids[basis_date] = outcome.meta.raw_record_id
        if outcome.was_duplicate:
            raw_duplicate_count += 1
        else:
            raw_inserted_count += 1
        return outcome.meta.received_at_utc

    provider = KrxHistoricalDataProvider(
        client,
        {target.instrument_id: target.symbol for target in targets},
        min_request_interval_seconds=min_request_interval_seconds,
        max_rate_limit_retries=max_rate_limit_retries,
        records_observer=record_daily_raw,
    )
    provider_registry = ProviderRegistry()
    provider_registry.register_historical_data("krx", provider)

    prefetch = provider.prefetch_latest_trading_days(
        end=end,
        minimum_trading_days=minimum_trading_days,
        max_lookback_calendar_days=max_lookback_calendar_days,
    )
    start = prefetch.trading_dates[0]
    actual_end = prefetch.trading_dates[-1]
    calendar_resolver = CalendarResolver(
        StaticHolidayProvider({Venue.KRX: set(prefetch.non_trading_weekdays)})
    )
    start_utc = local_datetime_to_utc_nanos(start, wall_time(0, 0), Venue.KRX)
    end_utc = local_datetime_to_utc_nanos(actual_end, wall_time(23, 59), Venue.KRX)

    summaries: list[InstrumentBackfillSummary] = []
    all_bars: list[Bar] = []
    normalized_inserted_count = 0
    normalized_duplicate_count = 0
    for target in targets:
        result = backfill_daily_bars(
            provider_registry,
            calendar_resolver,
            Venue.KRX,
            primary_provider_name="krx",
            instrument_id=target.instrument_id,
            start=start,
            end=actual_end,
            start_utc=start_utc,
            end_utc=end_utc,
            gate_registry=gate_registry,
            gate_as_of_utc=as_of_utc,
        )
        if not result.coverage.meets_minimum(minimum_trading_days):
            raise RuntimeError(
                f"{target.symbol} KRX 백필 커버리지 미달: "
                f"covered={result.coverage.covered_trading_days}, "
                f"missing={len(result.coverage.missing_dates)}"
            )
        for bar in result.bars:
            basis_date = utc_nanos_to_local_datetime(bar.bar_close_time_utc, Venue.KRX).date()
            raw_record_id = raw_record_ids[basis_date]
            normalized_id = f"krx:{_RAW_DATASET}:{target.symbol}:{basis_date:%Y%m%d}"
            stored = save_normalized_record_idempotent(
                engine,
                bar,
                created_at_utc=bar.received_time_utc,
                normalized_record_id=normalized_id,
            )
            if stored.was_duplicate:
                normalized_duplicate_count += 1
            else:
                normalized_inserted_count += 1
            add_lineage_edge(
                engine,
                LineageEdge(
                    edge_id=str(uuid.uuid4()),
                    run_id=run_id,
                    parent_record_id=raw_record_id,
                    parent_layer="raw",
                    child_record_id=stored.meta.normalized_record_id,
                    child_layer="normalized",
                    algorithm_version=_NORMALIZER_VERSION,
                    created_at_utc=time.time_ns(),
                ),
            )
        summaries.append(
            InstrumentBackfillSummary(
                instrument_id=target.instrument_id,
                symbol=target.symbol,
                bar_count=len(result.bars),
                latest_bar=result.bars[-1],
                result=result,
            )
        )
        all_bars.extend(result.bars)

    all_bars.sort(key=lambda bar: (bar.instrument_id, bar.bar_close_time_utc))
    snapshot = ParquetSnapshotWriter(data_root).write(
        _NORMALIZED_DATASET,
        all_bars,
        snapshot_id=run_id,
    )
    return KrxBackfillExecutionResult(
        collection_run_id=run_id,
        start=start,
        end=actual_end,
        trading_dates=prefetch.trading_dates,
        requested_dates=prefetch.fetched_dates,
        raw_inserted_count=raw_inserted_count,
        raw_duplicate_count=raw_duplicate_count,
        normalized_inserted_count=normalized_inserted_count,
        normalized_duplicate_count=normalized_duplicate_count,
        snapshot_id=snapshot.snapshot_id,
        snapshot_path=snapshot.files[0].path,
        instruments=tuple(summaries),
    )
