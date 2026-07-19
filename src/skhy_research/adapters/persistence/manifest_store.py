"""ExecutionManifest/LineageEdge/ExecutionEdge의 append-only PostgreSQL 저장소."""

from __future__ import annotations

from sqlalchemy import Engine, insert, select, update

from skhy_research.adapters.persistence.schema import (
    execution_edge,
    execution_manifest,
    lineage_edge,
)
from skhy_research.domain.experiment import ExecutionEdge, ExecutionManifest, LineageEdge


class DuplicateRunIdError(RuntimeError):
    """같은 run_id로 두 번째 manifest를 저장하려는 시도 (append-only 위반)."""


def save_manifest(engine: Engine, manifest: ExecutionManifest) -> None:
    with engine.begin() as conn:
        existing = conn.execute(
            select(execution_manifest.c.run_id).where(
                execution_manifest.c.run_id == manifest.run_id
            )
        ).first()
        if existing is not None:
            raise DuplicateRunIdError(f"run_id={manifest.run_id}는 이미 존재한다")
        conn.execute(
            insert(execution_manifest).values(
                run_id=manifest.run_id,
                repo_commit=manifest.repo_commit,
                repo_dirty=manifest.repo_dirty,
                python_version=manifest.python_version,
                lockfile_hash=manifest.lockfile_hash,
                config_env=manifest.config_env,
                config_hash=manifest.config_hash,
                component_versions=manifest.component_versions,
                seed=manifest.seed,
                started_at_utc=manifest.started_at_utc,
                ended_at_utc=manifest.ended_at_utc,
                data_snapshot_id=manifest.data_snapshot_id,
            )
        )


def finalize_manifest(
    engine: Engine, run_id: str, ended_at_utc: int, data_snapshot_id: str | None
) -> None:
    """종료시각·데이터 snapshot만 채운다. 다른 필드는 불변으로 남긴다."""
    with engine.begin() as conn:
        conn.execute(
            update(execution_manifest)
            .where(execution_manifest.c.run_id == run_id)
            .values(ended_at_utc=ended_at_utc, data_snapshot_id=data_snapshot_id)
        )


def get_manifest(engine: Engine, run_id: str) -> ExecutionManifest | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(execution_manifest).where(execution_manifest.c.run_id == run_id)
        ).mappings().first()
    if row is None:
        return None
    return ExecutionManifest(**dict(row))


def add_lineage_edge(engine: Engine, edge: LineageEdge) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(lineage_edge).values(
                edge_id=edge.edge_id,
                run_id=edge.run_id,
                parent_record_id=edge.parent_record_id,
                parent_layer=edge.parent_layer,
                child_record_id=edge.child_record_id,
                child_layer=edge.child_layer,
                algorithm_version=edge.algorithm_version,
                created_at_utc=edge.created_at_utc,
            )
        )


def add_execution_edge(engine: Engine, edge: ExecutionEdge) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(execution_edge).values(
                edge_id=edge.edge_id,
                run_id=edge.run_id,
                signal_id=edge.signal_id,
                risk_decision_id=edge.risk_decision_id,
                order_id=edge.order_id,
                fill_id=edge.fill_id,
                position_update_id=edge.position_update_id,
                created_at_utc=edge.created_at_utc,
            )
        )


def trace_lineage_for_record(engine: Engine, run_id: str, child_record_id: str) -> list[LineageEdge]:
    """주어진 레코드까지 이어지는 lineage edge를 부모 방향으로 재귀 탐색한다."""
    edges: list[LineageEdge] = []
    with engine.connect() as conn:
        frontier = [child_record_id]
        visited: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current in visited:
                continue
            visited.add(current)
            rows = conn.execute(
                select(lineage_edge).where(
                    (lineage_edge.c.run_id == run_id)
                    & (lineage_edge.c.child_record_id == current)
                )
            ).mappings().all()
            for row in rows:
                edges.append(LineageEdge(**dict(row)))
                frontier.append(row["parent_record_id"])
    return edges
