"""실 provider 경로의 날짜 공유 수집과 PostgreSQL/Parquet 영속 배선 검증."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from skhy_research.adapters.persistence.gate_decision_store import PostgresGateDecisionStore
from skhy_research.adapters.persistence.schema import (
    lineage_edge,
    normalized_record_catalog,
    raw_record_catalog,
)
from skhy_research.application.krx_backfill_runner import execute_krx_backfill
from skhy_research.domain.gate import GateDecision, GateStatus
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)

_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "krx" / "stk_bydd_trd_multi_symbol_20260717.json"
)
_AS_OF = 1_800_000_000_000_000_000


def _records(basis_date: date) -> list[dict[str, Any]]:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = payload["OutBlock_1"]
    for row in rows:
        row["BAS_DD"] = basis_date.strftime("%Y%m%d")
    return rows


class _FixtureKrxClient:
    def __init__(self, records_by_date: dict[date, list[dict[str, Any]]]) -> None:
        self.records_by_date = records_by_date
        self.calls: list[date] = []

    def capabilities(self) -> ProviderCatalogEntry:
        return ProviderCatalogEntry(
            provider_name="krx",
            port_type="historical_data",
            catalog_version="krx-integration-v1",
            capabilities=frozenset({ProviderCapability.HISTORICAL_BARS}),
            license_terms_url="https://example.test/krx-terms",
            storage_redistribution_allowed=False,
            last_verified_at_utc=_AS_OF,
            health_status=HealthStatus.HEALTHY,
        )

    def fetch_daily_stock_trades(self, trading_date: date) -> list[dict[str, Any]]:
        self.calls.append(trading_date)
        return self.records_by_date.get(trading_date, [])


def _confirmed(gate_id: str, digit: str) -> GateDecision:
    return GateDecision(
        gate_id=gate_id,
        status=GateStatus.CONFIRMED,
        evidence_url=f"https://example.test/{gate_id}",
        evidence_checksum=digit * 64,
        responsible_provider="test",
        conclusion="실 provider 백필 통합 검증",
        confirmed_at_utc=_AS_OF - 100,
        valid_until_utc=_AS_OF + 1_000_000_000,
        recorded_at_utc=_AS_OF,
    )


@pytest.mark.integration
def test_runner_loads_postgres_gates_calls_each_date_once_and_persists_bars(
    clean_pg, tmp_path: Path
) -> None:
    store = PostgresGateDecisionStore(clean_pg)
    store.save_decision(_confirmed("G-04", "4"))
    store.save_decision(_confirmed("G-06", "6"))
    wednesday = date(2026, 7, 15)
    thursday = date(2026, 7, 16)
    friday = date(2026, 7, 17)
    client = _FixtureKrxClient(
        {
            wednesday: _records(wednesday),
            thursday: [],
            friday: _records(friday),
        }
    )

    result = execute_krx_backfill(
        engine=clean_pg,
        data_root=tmp_path,
        client=client,
        end=friday,
        minimum_trading_days=2,
        min_request_interval_seconds=0,
        collection_run_id="krx-runner-integration",
        gate_as_of_utc=_AS_OF,
    )

    assert client.calls == [friday, thursday, wednesday]
    assert result.trading_dates == (wednesday, friday)
    assert [item.bar_count for item in result.instruments] == [2, 2]
    assert all(item.result.coverage.is_complete for item in result.instruments)
    assert result.raw_inserted_count == 3
    assert result.normalized_inserted_count == 4
    assert Path(result.snapshot_path).exists()

    with clean_pg.connect() as conn:
        raw_count = conn.scalar(select(func.count()).select_from(raw_record_catalog))
        normalized_count = conn.scalar(select(func.count()).select_from(normalized_record_catalog))
        lineage_count = conn.scalar(select(func.count()).select_from(lineage_edge))
    assert raw_count == 3
    assert normalized_count == 4
    assert lineage_count == 4
