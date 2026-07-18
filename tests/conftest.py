"""공유 pytest fixture.

`pg_engine`은 로컬/CI PostgreSQL에 연결을 시도하고, 연결 불가 시 해당 테스트를
skip한다 (실제 키·인프라 없이도 전체 스위트가 실패하지 않게 하기 위함). DB가
있는 환경에서는 실제 PostgreSQL 16 스키마를 검증하는 통합 테스트로 동작한다.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from skhy_research.adapters.persistence.db import build_engine
from skhy_research.adapters.persistence.schema import init_schema, metadata
from skhy_research.application.config import load_settings

_DEFAULT_LOCAL_DATABASE_URL = "postgresql+psycopg://skhy:skhy_local_dev@localhost:5432/skhy_research"


@pytest.fixture(scope="session")
def pg_engine():
    os.environ.setdefault("SKHY_DATABASE_URL", _DEFAULT_LOCAL_DATABASE_URL)
    settings = load_settings("local")
    try:
        engine = build_engine(settings)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 어떤 연결 실패든 skip으로 처리
        pytest.skip(f"PostgreSQL에 연결할 수 없어 통합 테스트를 건너뜀: {exc}")
        return
    init_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def clean_pg(pg_engine):
    """각 테스트 전 관련 테이블을 비운다. 운영 저장소는 append-only이며 이는 테스트 격리 전용이다."""
    with pg_engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(table.delete())
    yield pg_engine
