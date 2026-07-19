"""P0-02 통합 검증: PostgreSQL round-trip, append-only 제약, lineage 추적.

로컬 PostgreSQL(또는 CI의 docker-compose postgres)에 연결 가능해야 실행된다.
연결 불가 시 `pg_engine` fixture가 skip 처리한다.
"""

from __future__ import annotations

import time
import uuid

import pytest

from skhy_research.adapters.persistence.manifest_store import (
    DuplicateRunIdError,
    add_lineage_edge,
    get_manifest,
    save_manifest,
    trace_lineage_for_record,
)
from skhy_research.domain.experiment import ExecutionManifest, LineageEdge


def _make_manifest(run_id: str) -> ExecutionManifest:
    return ExecutionManifest(
        run_id=run_id,
        repo_commit="deadbeef",
        repo_dirty=False,
        python_version="3.12.13",
        lockfile_hash="lockhash",
        config_env="local",
        config_hash="confighash",
        component_versions={"strategy.h1": "1.0.0"},
        seed=7,
        started_at_utc=time.time_ns(),
    )


@pytest.mark.integration
def test_save_and_get_manifest_round_trip(clean_pg) -> None:
    run_id = str(uuid.uuid4())
    manifest = _make_manifest(run_id)

    save_manifest(clean_pg, manifest)
    fetched = get_manifest(clean_pg, run_id)

    assert fetched is not None
    assert fetched.run_id == run_id
    assert fetched.config_hash == "confighash"
    assert fetched.component_versions == {"strategy.h1": "1.0.0"}


@pytest.mark.integration
def test_duplicate_run_id_is_rejected(clean_pg) -> None:
    run_id = str(uuid.uuid4())
    save_manifest(clean_pg, _make_manifest(run_id))

    with pytest.raises(DuplicateRunIdError):
        save_manifest(clean_pg, _make_manifest(run_id))


@pytest.mark.integration
def test_lineage_traces_from_signal_back_to_raw(clean_pg) -> None:
    run_id = str(uuid.uuid4())
    save_manifest(clean_pg, _make_manifest(run_id))
    now = time.time_ns()

    add_lineage_edge(
        clean_pg,
        LineageEdge(
            edge_id=str(uuid.uuid4()),
            run_id=run_id,
            parent_record_id="raw-1",
            parent_layer="raw",
            child_record_id="normalized-1",
            child_layer="normalized",
            algorithm_version="normalizer@1.0.0",
            created_at_utc=now,
        ),
    )
    add_lineage_edge(
        clean_pg,
        LineageEdge(
            edge_id=str(uuid.uuid4()),
            run_id=run_id,
            parent_record_id="normalized-1",
            parent_layer="normalized",
            child_record_id="signal-1",
            child_layer="signal",
            algorithm_version="h1_strategy@1.0.0",
            created_at_utc=now,
        ),
    )

    edges = trace_lineage_for_record(clean_pg, run_id, "signal-1")
    parent_chain = {e.parent_record_id for e in edges}

    assert parent_chain == {"raw-1", "normalized-1"}
