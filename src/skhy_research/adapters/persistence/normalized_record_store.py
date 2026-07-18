"""정규화 도메인 객체를 lineage에서 역조회 가능한 PostgreSQL 레코드로 저장한다."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Engine, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from skhy_research.adapters.persistence.schema import normalized_record_catalog
from skhy_research.domain.normalized_record import NormalizedRecordMeta


class NormalizedRecordConflictError(RuntimeError):
    """같은 deterministic ID에 서로 다른 정규화 payload가 들어온 경우."""


@dataclass(frozen=True)
class NormalizedStoreOutcome:
    meta: NormalizedRecordMeta
    was_duplicate: bool


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


def save_normalized_record_idempotent(
    engine: Engine,
    record: BaseModel,
    *,
    created_at_utc: int,
    normalized_record_id: str,
) -> NormalizedStoreOutcome:
    """deterministic ID로 같은 Bar 재백필을 skip하고 내용 변경은 거부한다."""

    candidate = NormalizedRecordMeta(
        normalized_record_id=normalized_record_id,
        record_type=type(record).__name__,
        payload=record.model_dump(mode="json"),
        created_at_utc=created_at_utc,
    )
    statement = (
        pg_insert(normalized_record_catalog)
        .values(
            normalized_record_id=candidate.normalized_record_id,
            record_type=candidate.record_type,
            payload=candidate.payload,
            created_at_utc=candidate.created_at_utc,
        )
        .on_conflict_do_nothing(index_elements=(normalized_record_catalog.c.normalized_record_id,))
        .returning(normalized_record_catalog.c.normalized_record_id)
    )
    with engine.begin() as conn:
        inserted = conn.execute(statement).scalar_one_or_none()
        if inserted is not None:
            return NormalizedStoreOutcome(candidate, was_duplicate=False)
        row = conn.execute(
            select(normalized_record_catalog).where(
                normalized_record_catalog.c.normalized_record_id == normalized_record_id
            )
        ).mappings().one()

    existing = NormalizedRecordMeta(**dict(row))
    if existing.record_type != candidate.record_type or existing.payload != candidate.payload:
        raise NormalizedRecordConflictError(
            f"normalized_record_id={normalized_record_id}의 기존 payload와 백필 결과가 다르다"
        )
    return NormalizedStoreOutcome(existing, was_duplicate=True)


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
