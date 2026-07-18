"""PostgreSQL 접속 팩토리. 접속 문자열은 로그에 절대 출력하지 않는다."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine

from skhy_research.application.config import Settings


class DatabaseUrlNotConfiguredError(RuntimeError):
    pass


def resolve_database_url(settings: Settings) -> str:
    url = os.environ.get(settings.database_url_env)
    if not url:
        raise DatabaseUrlNotConfiguredError(
            f"환경변수 {settings.database_url_env}가 설정되지 않았다. .env.example 참고."
        )
    return url


def build_engine(settings: Settings, *, echo: bool = False) -> Engine:
    url = resolve_database_url(settings)
    return create_engine(url, echo=echo, future=True, pool_pre_ping=True)
