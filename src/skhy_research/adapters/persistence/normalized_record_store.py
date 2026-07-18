"""정규화 도메인 객체를 lineage에서 역조회 가능한 PostgreSQL 레코드로 저장한다."""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy import Engine, insert, select

from skhy_research.adapters.persistence.schema import normalized_record_catalog
from skhy_research.domain.normalized_record import NormalizedRecordMeta


def save_normalized_record(
    engine: Engine,
    record: BaseModel,
    *,
    created_at_utc: int,
    normalized_record_id: str | None = None,
) -> NormalizedRecordMeta:
    """정규화 객체를 JSON snapshot으로 append-only 저장하고 실제 record ID를 반환한다."""
    stored = NormalizedRecordMeta(
        normalized_record_id=normalized_record_id or str(uuid.uuid4()),
        record_type=type(record).__name__,
        payload=record.model_dump(mode="json"),
        created_at_utc=created_at_utc,
    )
    with engine.begin() as conn:
        conn.execute(
            insert(normalized_record_catalog).values(
                normalized_record_id=stored.normalized_record_id,
                record_type=stored.record_type,
                payload=stored.payload,
                created_at_utc=stored.created_at_utc,
            )
        )
    return stored


def get_normalized_record(
    engine: Engine, normalized_record_id: str
) -> NormalizedRecordMeta | None:
    """lineage child ID가 가리키는 실제 normalized snapshot을 조회한다."""
    with engine.connect() as conn:
        row = conn.execute(
            select(normalized_record_catalog).where(
                normalized_record_catalog.c.normalized_record_id == normalized_record_id
            )
        ).mappings().one_or_none()
    return NormalizedRecordMeta(**dict(row)) if row is not None else None
