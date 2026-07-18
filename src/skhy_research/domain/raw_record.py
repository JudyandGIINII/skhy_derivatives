"""원시 레코드 계보 메타데이터 (PRD 8.1, FR-03, FR-16).

원시 payload 자체는 append-only 압축 파일로 디스크에 저장하고, 이 메타데이터만
PostgreSQL catalog에 남긴다. `(source, dataset, dedupe_key)`는 단일 canonical
레코드를 가리킨다. `conflict_with`는 기존 catalog와의 하위호환을 위해 유지하지만
신규 recorder는 같은 key의 후보 payload를 별도 행으로 추가하지 않고 충돌 결과로
보고한 뒤 canonical 레코드를 보존한다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from skhy_research.domain.market import EpochNanos


class RawRecordMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw_record_id: str
    source: str
    dataset: str
    dedupe_key: str
    payload_checksum: str  # sha256 hex
    received_at_utc: EpochNanos
    collection_run_id: str
    provider_sequence: str | None = None
    storage_path: str
    conflict_with: str | None = None
