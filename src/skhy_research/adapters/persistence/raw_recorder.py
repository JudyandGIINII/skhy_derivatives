"""원시 데이터 불변 저장·재시작 catch-up (P0-08, FR-03, FR-16).

- payload는 `source/dataset/event_date/hour` 파티션의 gzip 파일로 append-only 저장한다.
- 같은 `dedupe_key`가 같은 checksum으로 다시 들어오면 조용히 idempotent skip한다
  (재시작 후 중복 없는 재개).
- 같은 `dedupe_key`인데 checksum이 다르면 충돌로 보고 canonical 레코드를 유지한다.
  후보 payload는 `RawStoreOutcome.was_conflict=True`로 보고하고 중복 파일·행은 남기지 않는다.
- checkpoint(cursor)는 소스별로 독립 관리해 한 공급자의 장애가 다른 수집을 막지 않는다.
"""

from __future__ import annotations

import gzip
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from skhy_research.adapters.persistence.schema import ingestion_checkpoint, raw_record_catalog
from skhy_research.domain.raw_record import RawRecordMeta


class RawRecordCorruptionError(RuntimeError):
    """저장된 파일의 내용이 catalog의 checksum과 다를 때 — 무결성 침해."""


@dataclass(frozen=True)
class RawStoreOutcome:
    meta: RawRecordMeta
    was_duplicate: bool  # True면 기존 레코드를 그대로 반환(idempotent skip)
    was_conflict: bool  # True면 key는 같지만 내용이 달라 canonical 기존 레코드를 유지함


def compute_dedupe_key(
    source: str, dataset: str, event_type: str, event_time_utc: int, payload_checksum: str
) -> str:
    """공급자 sequence가 없을 때의 기본 dedupe key (PRD 8.2)."""
    raw = f"{source}|{dataset}|{event_type}|{event_time_utc}|{payload_checksum}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class RawRecorder:
    def __init__(self, engine: Engine, data_root: Path) -> None:
        self._engine = engine
        self._data_root = data_root

    def store(
        self,
        source: str,
        dataset: str,
        payload: bytes,
        received_at_utc: int,
        collection_run_id: str,
        dedupe_key: str,
        provider_sequence: str | None = None,
    ) -> RawStoreOutcome:
        checksum = compute_checksum(payload)
        raw_record_id = str(uuid.uuid4())
        storage_path = self._partition_path(source, dataset, received_at_utc, raw_record_id)
        candidate = RawRecordMeta(
            raw_record_id=raw_record_id,
            source=source,
            dataset=dataset,
            dedupe_key=dedupe_key,
            payload_checksum=checksum,
            received_at_utc=received_at_utc,
            collection_run_id=collection_run_id,
            provider_sequence=provider_sequence,
            storage_path=str(storage_path),
            conflict_with=None,
        )
        self._write_payload_file(storage_path, payload)

        inserted = False
        existing_meta: RawRecordMeta | None = None
        try:
            with self._engine.begin() as conn:
                inserted = self._insert_candidate(conn, candidate)
                if not inserted:
                    existing = conn.execute(
                        select(raw_record_catalog).where(
                            (raw_record_catalog.c.source == source)
                            & (raw_record_catalog.c.dataset == dataset)
                            & (raw_record_catalog.c.dedupe_key == dedupe_key)
                        )
                    ).mappings().one_or_none()
                    if existing is None:
                        raise RuntimeError("dedupe 충돌 후 canonical raw 레코드를 찾을 수 없다")
                    existing_meta = RawRecordMeta(**dict(existing))
        except Exception:
            storage_path.unlink(missing_ok=True)
            raise

        if inserted:
            return RawStoreOutcome(meta=candidate, was_duplicate=False, was_conflict=False)

        # ON CONFLICT에서 패한 후보 파일은 catalog에 연결되지 않으므로 즉시 제거한다.
        storage_path.unlink(missing_ok=True)
        assert existing_meta is not None  # 위 트랜잭션에서 보장됨
        self._verify_stored_payload(
            Path(existing_meta.storage_path), existing_meta.payload_checksum
        )
        same_payload = existing_meta.payload_checksum == checksum
        return RawStoreOutcome(
            meta=existing_meta,
            was_duplicate=same_payload,
            was_conflict=not same_payload,
        )

    def _write_payload_file(self, storage_path: Path, payload: bytes) -> None:
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with gzip.open(storage_path, "xb") as fh:  # 'x': 기존 원본 덮어쓰기 금지
                fh.write(payload)
        except Exception:
            storage_path.unlink(missing_ok=True)
            raise

    def _insert_candidate(self, conn, candidate: RawRecordMeta) -> bool:  # noqa: ANN001
        statement = (
            pg_insert(raw_record_catalog)
            .values(
                raw_record_id=candidate.raw_record_id,
                source=candidate.source,
                dataset=candidate.dataset,
                dedupe_key=candidate.dedupe_key,
                payload_checksum=candidate.payload_checksum,
                received_at_utc=candidate.received_at_utc,
                collection_run_id=candidate.collection_run_id,
                provider_sequence=candidate.provider_sequence,
                storage_path=candidate.storage_path,
                conflict_with=candidate.conflict_with,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    raw_record_catalog.c.source,
                    raw_record_catalog.c.dataset,
                    raw_record_catalog.c.dedupe_key,
                )
            )
            .returning(raw_record_catalog.c.raw_record_id)
        )
        return conn.execute(statement).scalar_one_or_none() is not None

    def _partition_path(
        self, source: str, dataset: str, received_at_utc: int, raw_record_id: str
    ) -> Path:
        dt = datetime.fromtimestamp(received_at_utc / 1_000_000_000, tz=UTC)
        return (
            self._data_root
            / "raw"
            / source
            / dataset
            / dt.strftime("%Y-%m-%d")
            / dt.strftime("%H")
            / f"{raw_record_id}.json.gz"
        )

    def _verify_stored_payload(self, storage_path: Path, expected_checksum: str) -> None:
        with gzip.open(storage_path, "rb") as fh:
            actual_checksum = compute_checksum(fh.read())
        if actual_checksum != expected_checksum:
            raise RawRecordCorruptionError(
                f"{storage_path}의 저장된 내용이 catalog checksum과 다르다"
            )

    # --- checkpoint (재시작 catch-up) ---
    def get_checkpoint(self, source: str, dataset: str) -> str | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(ingestion_checkpoint.c.cursor).where(
                    (ingestion_checkpoint.c.source == source)
                    & (ingestion_checkpoint.c.dataset == dataset)
                )
            ).first()
        return row[0] if row else None

    def advance_checkpoint(self, source: str, dataset: str, cursor: str, updated_at_utc: int) -> None:
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(ingestion_checkpoint.c.source).where(
                    (ingestion_checkpoint.c.source == source)
                    & (ingestion_checkpoint.c.dataset == dataset)
                )
            ).first()
            if existing is None:
                conn.execute(
                    insert(ingestion_checkpoint).values(
                        source=source, dataset=dataset, cursor=cursor, updated_at_utc=updated_at_utc
                    )
                )
            else:
                conn.execute(
                    update(ingestion_checkpoint)
                    .where(
                        (ingestion_checkpoint.c.source == source)
                        & (ingestion_checkpoint.c.dataset == dataset)
                    )
                    .values(cursor=cursor, updated_at_utc=updated_at_utc)
                )
