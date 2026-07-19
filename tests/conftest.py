"""공유 pytest fixture.

`pg_engine`은 `SKHY_TEST_DATABASE_URL`로 지정한 테스트 전용 PostgreSQL에만
연결한다. 기본 개발 DB나 `SKHY_DATABASE_URL`의 DB를 테스트 대상으로 지정하면
데이터 삭제 전에 즉시 실패한다. 연결 불가 시에는 기존처럼 통합 테스트를 skip한다.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

from skhy_research.adapters.persistence.db import build_engine
from skhy_research.adapters.persistence.schema import init_schema, metadata
from skhy_research.application.config import load_settings

_TEST_DATABASE_URL_ENV = "SKHY_TEST_DATABASE_URL"
_DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://skhy:skhy_local_dev@localhost:5432/skhy_research_test"
)
_DEFAULT_DEV_DATABASE_NAME = "skhy_research"


def _database_name(database_url: str) -> str:
    name = make_url(database_url).database
    if not name:
        raise ValueError("테스트 PostgreSQL URL에 데이터베이스 이름이 필요함")
    return name


def _validate_test_database_url(database_url: str) -> URL:
    """개발 DB를 테스트 대상으로 지정하는 위험한 설정을 거부한다."""
    test_url = make_url(database_url)
    test_database_name = _database_name(database_url)
    protected_names = {_DEFAULT_DEV_DATABASE_NAME}

    configured_dev_url = os.environ.get("SKHY_DATABASE_URL")
    if configured_dev_url:
        protected_names.add(_database_name(configured_dev_url))

    if test_database_name in protected_names:
        raise ValueError(
            f"{_TEST_DATABASE_URL_ENV}는 개발 DB가 아닌 테스트 전용 DB를 가리켜야 함 "
            f"(거부된 DB 이름: {test_database_name})"
        )
    return test_url


def _sqlstate(exc: BaseException) -> str | None:
    current: BaseException | None = exc
    while current is not None:
        state = getattr(current, "sqlstate", None)
        if isinstance(state, str):
            return state
        current = current.__cause__ or current.__context__
    return None


def _create_test_database(test_url: URL) -> None:
    """접속 역할에 권한이 있으면 누락된 테스트 DB를 멱등 생성한다."""
    database_name = test_url.database
    if database_name is None:  # pragma: no cover - URL 검증에서 먼저 차단됨
        raise ValueError("테스트 PostgreSQL URL에 데이터베이스 이름이 필요함")

    admin_engine = create_engine(
        test_url.set(database="postgres"),
        future=True,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        with admin_engine.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": database_name},
            )
            if exists is None:
                quoted_name = conn.dialect.identifier_preparer.quote_identifier(database_name)
                try:
                    conn.exec_driver_sql(f"CREATE DATABASE {quoted_name}")
                except DBAPIError as exc:
                    if _sqlstate(exc) != "42P04":  # duplicate_database: 동시 생성만 허용
                        raise
    finally:
        admin_engine.dispose()


def _build_test_engine(settings, test_url: URL) -> Engine:
    engine = build_engine(settings)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except DBAPIError as exc:
        engine.dispose()
        if _sqlstate(exc) != "3D000":  # invalid_catalog_name
            raise
        _create_test_database(test_url)
        engine = build_engine(settings)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    return engine


@pytest.fixture(scope="session")
def pg_engine() -> Generator[Engine, None, None]:
    os.environ.setdefault(_TEST_DATABASE_URL_ENV, _DEFAULT_TEST_DATABASE_URL)
    test_database_url = os.environ[_TEST_DATABASE_URL_ENV]
    try:
        test_url = _validate_test_database_url(test_database_url)
    except (TypeError, ValueError) as exc:
        pytest.fail(str(exc), pytrace=False)

    settings = load_settings("local").model_copy(
        update={"database_url_env": _TEST_DATABASE_URL_ENV}
    )
    try:
        engine = _build_test_engine(settings, test_url)
    except Exception as exc:  # noqa: BLE001 - 어떤 연결 실패든 skip으로 처리
        pytest.skip(f"테스트 전용 PostgreSQL에 연결할 수 없어 통합 테스트를 건너뜀: {exc}")
    init_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def clean_pg(pg_engine: Engine) -> Generator[Engine, None, None]:
    """각 테스트 전 관련 테이블을 비운다. 운영 저장소는 append-only이며 이는 테스트 격리 전용이다."""
    with pg_engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(table.delete())
    yield pg_engine
