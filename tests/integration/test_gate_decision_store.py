"""GateDecision PostgreSQL journal과 runtime loader 통합 검증."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select

from skhy_research.adapters.persistence.gate_decision_store import (
    PostgresGateDecisionStore,
)
from skhy_research.adapters.persistence.schema import gate_decision
from skhy_research.application.gate_registry import InvalidGateDecisionError
from skhy_research.application.gate_registry_loader import load_gate_registry
from skhy_research.domain.gate import GateDecision, GateStatus

_NOW = 1_800_000_000_000_000_000


def _confirmed_decision(gate_id: str, recorded_at_utc: int = _NOW) -> GateDecision:
    return GateDecision(
        gate_id=gate_id,
        status=GateStatus.CONFIRMED,
        evidence_url=f"https://example.com/test-evidence/{gate_id}",
        evidence_checksum="a" * 64,
        responsible_provider="synthetic-integration-test",
        conclusion=f"{gate_id} synthetic integration test decision",
        confirmed_at_utc=recorded_at_utc,
        valid_until_utc=recorded_at_utc + 90_000_000_000_000,
        recorded_at_utc=recorded_at_utc,
    )


@pytest.mark.integration
def test_store_preserves_history_and_loads_latest_decision_per_gate(clean_pg) -> None:
    store = PostgresGateDecisionStore(clean_pg)
    store.save_decision(
        GateDecision(
            gate_id="G-04",
            status=GateStatus.IN_REVIEW,
            recorded_at_utc=_NOW - 1,
        )
    )
    store.save_decision(_confirmed_decision("G-04"))
    store.save_decision(
        GateDecision(gate_id="G-06", status=GateStatus.REJECTED, recorded_at_utc=_NOW)
    )

    loaded = store.load_all_decisions()

    assert [(decision.gate_id, decision.status) for decision in loaded] == [
        ("G-04", GateStatus.CONFIRMED),
        ("G-06", GateStatus.REJECTED),
    ]
    with clean_pg.connect() as conn:
        history_count = conn.scalar(select(func.count()).select_from(gate_decision))
    assert history_count == 3


@pytest.mark.integration
def test_loader_rejects_incomplete_confirmed_decision_from_postgres(clean_pg) -> None:
    store = PostgresGateDecisionStore(clean_pg)
    store.save_decision(
        GateDecision(gate_id="G-04", status=GateStatus.CONFIRMED, recorded_at_utc=_NOW)
    )

    with pytest.raises(InvalidGateDecisionError, match="필드 누락"):
        load_gate_registry(store)


@pytest.mark.integration
def test_0004_gate_decision_migration_is_idempotent(clean_pg) -> None:
    migration_path = Path(__file__).parents[2] / "migrations" / "0004_gate_decision.sql"
    migration_sql = migration_path.read_text(encoding="utf-8")

    gate_decision.drop(clean_pg, checkfirst=True)
    with clean_pg.begin() as conn:
        conn.exec_driver_sql(migration_sql)
        conn.exec_driver_sql(migration_sql)

    column_names = {column["name"] for column in inspect(clean_pg).get_columns("gate_decision")}
    assert column_names == {
        "gate_id",
        "status",
        "evidence_url",
        "evidence_checksum",
        "responsible_provider",
        "conclusion",
        "confirmed_at_utc",
        "valid_until_utc",
        "recorded_at_utc",
    }
