-- P0-11 보강: gate 결정을 PostgreSQL append-only journal로 영속화한다.
-- (gate_id, recorded_at_utc) 복합 기본키는 같은 gate의 이력을 보존하면서
-- gate_id별 최신 recorded_at_utc 행을 효율적으로 조회할 수 있게 한다.

CREATE TABLE IF NOT EXISTS gate_decision (
    gate_id               VARCHAR NOT NULL,
    status                VARCHAR NOT NULL,
    evidence_url          VARCHAR,
    evidence_checksum     VARCHAR,
    responsible_provider VARCHAR,
    conclusion            VARCHAR,
    confirmed_at_utc      BIGINT,
    valid_until_utc       BIGINT,
    recorded_at_utc       BIGINT NOT NULL,
    PRIMARY KEY (gate_id, recorded_at_utc)
);
