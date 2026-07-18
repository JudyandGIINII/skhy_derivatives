-- P0-02 초기 스키마 스냅샷. 실제 적용은
-- `skhy_research.adapters.persistence.schema.init_schema()`(SQLAlchemy metadata.create_all,
-- checkfirst=True)로 멱등하게 수행한다. 이 파일은 문서화된 스냅샷이며, 스키마에
-- breaking change가 생기는 시점부터 Alembic 버전 마이그레이션으로 전환한다
-- (implementation_plan.md 9장 "스키마 변경" 정책).

CREATE TABLE IF NOT EXISTS execution_manifest (
    run_id             VARCHAR PRIMARY KEY,
    repo_commit        VARCHAR NOT NULL,
    repo_dirty         BOOLEAN NOT NULL,
    python_version     VARCHAR NOT NULL,
    lockfile_hash      VARCHAR NOT NULL,
    config_env         VARCHAR NOT NULL,
    config_hash        VARCHAR NOT NULL,
    component_versions JSON NOT NULL,
    seed               BIGINT NOT NULL,
    started_at_utc     BIGINT NOT NULL,
    ended_at_utc       BIGINT,
    data_snapshot_id   VARCHAR
);

CREATE TABLE IF NOT EXISTS lineage_edge (
    edge_id            VARCHAR PRIMARY KEY,
    run_id             VARCHAR NOT NULL,
    parent_record_id   VARCHAR NOT NULL,
    parent_layer       VARCHAR NOT NULL,
    child_record_id    VARCHAR NOT NULL,
    child_layer        VARCHAR NOT NULL,
    algorithm_version  VARCHAR NOT NULL,
    created_at_utc     BIGINT NOT NULL,
    CONSTRAINT uq_lineage_edge_triplet UNIQUE (run_id, parent_record_id, child_record_id)
);

CREATE TABLE IF NOT EXISTS execution_edge (
    edge_id             VARCHAR PRIMARY KEY,
    run_id              VARCHAR NOT NULL,
    signal_id           VARCHAR,
    risk_decision_id    VARCHAR,
    order_id            VARCHAR,
    fill_id             VARCHAR,
    position_update_id  VARCHAR,
    created_at_utc      BIGINT NOT NULL
);
