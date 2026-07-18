"""KRX ETF/ETN 일별 원문을 daily-proxy 입력으로 append-only 백필한다."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Engine

from skhy_research.adapters.persistence.gate_decision_store import PostgresGateDecisionStore
from skhy_research.adapters.persistence.manifest_store import add_lineage_edge
from skhy_research.adapters.persistence.normalized_record_store import (
    save_normalized_record_idempotent,
)
from skhy_research.adapters.persistence.raw_recorder import RawRecorder
from skhy_research.application.gate_registry_loader import load_gate_registry
from skhy_research.application.instrument_master import InstrumentMaster
from skhy_research.application.krx_backfill import assert_backfill_gates
from skhy_research.application.leverage_universe_discovery import (
    discover_and_register_krx_leveraged_universe,
)
from skhy_research.domain.enums import AssetClass
from skhy_research.domain.experiment import LineageEdge
from skhy_research.domain.krx_etp import KrxEtpDailySnapshot
from skhy_research.domain.provider_capability import ProviderCatalogEntry
from skhy_research.ports.errors import ProviderRateLimitError

_ETF_DATASET = "etf_bydd_trd"
_ETN_DATASET = "etn_bydd_trd"
_NORMALIZER_VERSION = "krx_etp_daily_snapshot@1.0.0"


class KrxEtpBackfillClient(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def fetch_daily_etf_trades(self, trading_date: date) -> list[dict[str, Any]]: ...

    def fetch_daily_etn_trades(self, trading_date: date) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class KrxEtpBackfillExecutionResult:
    collection_run_id: str
    trading_dates: tuple[date, ...]
    raw_inserted_count: int
    raw_duplicate_count: int
    normalized_inserted_count: int
    normalized_duplicate_count: int
    product_observation_count: int
    excluded_observation_count: int
    product_symbols: tuple[str, ...]


class KrxEtpRawPersistenceConflictError(RuntimeError):
    """같은 endpoint·기준일의 기존 raw와 새 응답 checksum이 다름."""


class _CachedEtpRowsClient:
    def __init__(
        self,
        trading_date: date,
        etf_rows: list[dict[str, Any]],
        etn_rows: list[dict[str, Any]],
    ) -> None:
        self._trading_date = trading_date
        self._etf_rows = etf_rows
        self._etn_rows = etn_rows

    def fetch_daily_etf_trades(self, trading_date: date) -> list[dict[str, Any]]:
        if trading_date != self._trading_date:
            raise ValueError("cached ETF 기준일과 요청일이 다름")
        return self._etf_rows

    def fetch_daily_etn_trades(self, trading_date: date) -> list[dict[str, Any]]:
        if trading_date != self._trading_date:
            raise ValueError("cached ETN 기준일과 요청일이 다름")
        return self._etn_rows


def execute_krx_etp_backfill(
    *,
    engine: Engine,
    data_root: Path,
    client: KrxEtpBackfillClient,
    trading_dates: Iterable[date],
    target_underlyings: frozenset[str] = frozenset({"SK하이닉스"}),
    min_request_interval_seconds: float = 0.2,
    max_rate_limit_retries: int = 4,
    collection_run_id: str | None = None,
    gate_as_of_utc: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    clock_ns: Callable[[], int] = time.time_ns,
) -> KrxEtpBackfillExecutionResult:
    """주어진 실제 거래일을 endpoint별 한 번씩 조회하고 raw·normalized로 저장한다."""

    dates = tuple(sorted(set(trading_dates)))
    if not dates:
        raise ValueError("KRX ETP 백필 거래일은 비어 있을 수 없다")
    if min_request_interval_seconds < 0:
        raise ValueError("min_request_interval_seconds는 음수일 수 없다")
    if max_rate_limit_retries < 0:
        raise ValueError("max_rate_limit_retries는 음수일 수 없다")
    if not target_underlyings:
        raise ValueError("target_underlyings는 비어 있을 수 없다")

    run_id = collection_run_id or str(uuid.uuid4())
    as_of_utc = gate_as_of_utc or clock_ns()
    registry = load_gate_registry(PostgresGateDecisionStore(engine))
    assert_backfill_gates(registry, as_of_utc)

    recorder = RawRecorder(engine, data_root)
    provider_catalog = client.capabilities()
    raw_inserted_count = 0
    raw_duplicate_count = 0
    normalized_inserted_count = 0
    normalized_duplicate_count = 0
    product_observation_count = 0
    excluded_observation_count = 0
    product_symbols: set[str] = set()
    last_request_at: float | None = None

    def fetch_with_pacing(
        fetch: Callable[[date], list[dict[str, Any]]], trading_date: date
    ) -> list[dict[str, Any]]:
        nonlocal last_request_at
        for attempt in range(max_rate_limit_retries + 1):
            if last_request_at is not None:
                remaining = min_request_interval_seconds - (monotonic() - last_request_at)
                if remaining > 0:
                    sleep(remaining)
            try:
                rows = fetch(trading_date)
            except ProviderRateLimitError as exc:
                last_request_at = monotonic()
                if attempt >= max_rate_limit_retries:
                    raise
                backoff = min_request_interval_seconds * (2**attempt)
                sleep(max(exc.retry_after_seconds, backoff))
                continue
            last_request_at = monotonic()
            return rows
        raise AssertionError("KRX ETP retry loop exhausted")

    def persist_raw(
        dataset: str,
        trading_date: date,
        rows: list[dict[str, Any]],
    ) -> tuple[str, int]:
        nonlocal raw_inserted_count, raw_duplicate_count
        payload = json.dumps(
            {"OutBlock_1": rows},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        outcome = recorder.store(
            source="krx",
            dataset=dataset,
            payload=payload,
            received_at_utc=clock_ns(),
            collection_run_id=run_id,
            dedupe_key=f"basDd={trading_date:%Y%m%d}",
            provider_catalog=provider_catalog,
            provider_sequence=f"{trading_date:%Y%m%d}",
        )
        if outcome.was_conflict:
            raise KrxEtpRawPersistenceConflictError(
                f"KRX {dataset} {trading_date:%Y-%m-%d} raw checksum 충돌"
            )
        if outcome.was_duplicate:
            raw_duplicate_count += 1
        else:
            raw_inserted_count += 1
        return outcome.meta.raw_record_id, outcome.meta.received_at_utc

    for trading_date in dates:
        etf_rows = fetch_with_pacing(client.fetch_daily_etf_trades, trading_date)
        etf_raw_id, etf_received_at = persist_raw(_ETF_DATASET, trading_date, etf_rows)
        etn_rows = fetch_with_pacing(client.fetch_daily_etn_trades, trading_date)
        etn_raw_id, etn_received_at = persist_raw(_ETN_DATASET, trading_date, etn_rows)

        discovery = discover_and_register_krx_leveraged_universe(
            _CachedEtpRowsClient(trading_date, etf_rows, etn_rows),
            InstrumentMaster(),
            trading_date,
            target_underlyings=target_underlyings,
        )
        excluded_observation_count += len(discovery.exclusions)
        for product in discovery.products:
            if product.nav_or_indicative_value is None or product.listed_shares is None:
                excluded_observation_count += 1
                continue
            if product.asset_class is AssetClass.LEVERAGED_ETF:
                raw_record_id = etf_raw_id
                received_at_utc = etf_received_at
            elif product.asset_class is AssetClass.LEVERAGED_ETN:
                raw_record_id = etn_raw_id
                received_at_utc = etn_received_at
            else:  # pragma: no cover - discovery는 ETF/ETN만 반환함
                raise ValueError(f"지원하지 않는 KRX ETP asset class: {product.asset_class}")

            snapshot = KrxEtpDailySnapshot(
                fund_id=product.instrument_id,
                source_symbol=product.source_symbol,
                display_name=product.display_name,
                asset_class=product.asset_class,
                underlying_name=product.underlying_name,
                leverage_factor=product.leverage_factor,
                basis_date=product.basis_date,
                nav_or_indicative_value=product.nav_or_indicative_value,
                listed_shares=product.listed_shares,
                raw_record_id=raw_record_id,
            )
            normalized_id = f"krx:etp_daily:{product.source_symbol}:{trading_date:%Y%m%d}"
            stored = save_normalized_record_idempotent(
                engine,
                snapshot,
                created_at_utc=received_at_utc,
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
                    child_record_id=normalized_id,
                    child_layer="normalized",
                    algorithm_version=_NORMALIZER_VERSION,
                    created_at_utc=clock_ns(),
                ),
            )
            product_observation_count += 1
            product_symbols.add(product.source_symbol)

    return KrxEtpBackfillExecutionResult(
        collection_run_id=run_id,
        trading_dates=dates,
        raw_inserted_count=raw_inserted_count,
        raw_duplicate_count=raw_duplicate_count,
        normalized_inserted_count=normalized_inserted_count,
        normalized_duplicate_count=normalized_duplicate_count,
        product_observation_count=product_observation_count,
        excluded_observation_count=excluded_observation_count,
        product_symbols=tuple(sorted(product_symbols)),
    )
