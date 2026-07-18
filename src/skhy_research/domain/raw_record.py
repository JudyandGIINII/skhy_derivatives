"""원시 레코드 계보 메타데이터 (PRD 8.1, FR-03, FR-16).

원시 payload 자체는 append-only 압축 파일로 디스크에 저장하고, 이 메타데이터만
PostgreSQL catalog에 남긴다. `conflict_with`는 같은 `dedupe_key`인데
`payload_checksum`이 다른 경우에만 채워진다 — 충돌 레코드를 조용히 버리지
않고 둘 다 보존하기 위함이다 (PRD 8.2 "충돌 레코드는 조용히 버리지 않고
중복·불일치 상태와 원본 ID를 보존한다").
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
