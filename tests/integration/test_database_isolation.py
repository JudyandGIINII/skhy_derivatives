"""PostgreSQL 통합 테스트가 개발 DB와 분리되었는지 검증한다."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url


@pytest.mark.integration
def test_pg_engine_targets_dedicated_test_database(pg_engine: Engine) -> None:
    configured_test_url = os.environ["SKHY_TEST_DATABASE_URL"]
    expected_database = make_url(configured_test_url).database

    with pg_engine.connect() as conn:
        actual_database = conn.scalar(text("SELECT current_database()"))

    assert actual_database == expected_database
    assert actual_database != "skhy_research"

    configured_dev_url = os.environ.get("SKHY_DATABASE_URL")
    if configured_dev_url:
        assert actual_database != make_url(configured_dev_url).database
