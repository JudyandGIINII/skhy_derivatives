"""실행 manifest·lineage·execution edge PostgreSQL 스키마 (P0-02).

스키마는 SQLAlchemy MetaData로 단일 정의하고 `init_schema()`로 멱등하게
적용한다. `migrations/0001_initial_schema.sql`은 동일 스키마의 문서화된
스냅샷이며, breaking change부터는 Alembic 버전 마이그레이션으로 전환한다
(`implementation_plan.md` 9장 스키마 변경 정책).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Engine,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()

execution_manifest = Table(
    "execution_manifest",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("repo_commit", String, nullable=False),
    Column("repo_dirty", Boolean, nullable=False),
    Column("python_version", String, nullable=False),
    Column("lockfile_hash", String, nullable=False),
    Column("config_env", String, nullable=False),
    Column("config_hash", String, nullable=False),
    Column("component_versions", JSON, nullable=False),
    Column("seed", BigInteger, nullable=False),
    Column("started_at_utc", BigInteger, nullable=False),
    Column("ended_at_utc", BigInteger, nullable=True),
    Column("data_snapshot_id", String, nullable=True),
)

lineage_edge = Table(
    "lineage_edge",
    metadata,
    Column("edge_id", String, primary_key=True),
    Column("run_id", String, nullable=False),
    Column("parent_record_id", String, nullable=False),
    Column("parent_layer", String, nullable=False),
    Column("child_record_id", String, nullable=False),
    Column("child_layer", String, nullable=False),
    Column("algorithm_version", String, nullable=False),
    Column("created_at_utc", BigInteger, nullable=False),
    UniqueConstraint(
        "run_id", "parent_record_id", "child_record_id", name="uq_lineage_edge_triplet"
    ),
)

execution_edge = Table(
    "execution_edge",
    metadata,
    Column("edge_id", String, primary_key=True),
    Column("run_id", String, nullable=False),
    Column("signal_id", String, nullable=True),
    Column("risk_decision_id", String, nullable=True),
    Column("order_id", String, nullable=True),
    Column("fill_id", String, nullable=True),
    Column("position_update_id", String, nullable=True),
    Column("created_at_utc", BigInteger, nullable=False),
)


def init_schema(engine: Engine) -> None:
    """테이블이 없으면 생성한다 (멱등). 기존 테이블·데이터는 건드리지 않는다."""
    metadata.create_all(engine, checkfirst=True)
