"""DB에 영속된 normalized 도메인 레코드의 catalog metadata."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from skhy_research.domain.market import EpochNanos


class NormalizedRecordMeta(BaseModel):
    """lineage child ID와 실제 정규화 payload를 연결하는 불변 레코드."""

    model_config = ConfigDict(frozen=True)

    normalized_record_id: str
    record_type: str
    payload: dict[str, Any]
    created_at_utc: EpochNanos
