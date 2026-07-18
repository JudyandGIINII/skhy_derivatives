"""G-01~G-08 구현 착수 게이트 도메인 타입 (PRD 19장)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class GateStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    IN_REVIEW = "IN_REVIEW"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class GateDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate_id: str  # "G-01".."G-08"
    question: str
    default_action_if_unresolved: str


class GateDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate_id: str
    status: GateStatus
    evidence_url: str | None = None
    evidence_checksum: str | None = None
    responsible_provider: str | None = None
    conclusion: str | None = None
    confirmed_at_utc: int | None = None
    valid_until_utc: int | None = None
    recorded_at_utc: int
