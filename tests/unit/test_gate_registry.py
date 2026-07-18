"""P0-11 검증: G-01~G-08 gate 상태 관리와 기본 BLOCK 원칙 (PRD 19장)."""

from __future__ import annotations

import pytest

from skhy_research.application.gate_registry import (
    GATE_DEFINITIONS,
    GateRegistry,
    InvalidGateDecisionError,
    UnknownGateError,
)
from skhy_research.domain.gate import GateDecision, GateStatus

_NOW = 1_800_000_000_000_000_000


def test_all_eight_gates_are_defined() -> None:
    assert set(GATE_DEFINITIONS.keys()) == {f"G-0{i}" for i in range(1, 9)}


def test_unrecorded_gate_defaults_to_unknown_and_blocks() -> None:
    registry = GateRegistry()
    assert registry.effective_status("G-01", _NOW) == GateStatus.UNKNOWN
    assert registry.blocks("G-01", _NOW) is True


def test_recording_unknown_gate_id_raises() -> None:
    registry = GateRegistry()
    with pytest.raises(UnknownGateError):
        registry.record_decision(
            GateDecision(gate_id="G-99", status=GateStatus.CONFIRMED, evidence_url="x", recorded_at_utc=_NOW)
        )


def test_confirmed_without_evidence_url_is_rejected() -> None:
    registry = GateRegistry()
    with pytest.raises(InvalidGateDecisionError):
        registry.record_decision(
            GateDecision(gate_id="G-01", status=GateStatus.CONFIRMED, recorded_at_utc=_NOW)
        )


def test_confirmed_with_evidence_resolves_and_unblocks() -> None:
    registry = GateRegistry()
    registry.record_decision(
        GateDecision(
            gate_id="G-06",
            status=GateStatus.CONFIRMED,
            evidence_url="https://example.com/terms",
            confirmed_at_utc=_NOW,
            valid_until_utc=_NOW + 90_000_000_000_000,  # 90초 뒤(테스트용)
            recorded_at_utc=_NOW,
        )
    )

    assert registry.is_resolved("G-06", _NOW) is True
    assert registry.blocks("G-06", _NOW) is False


def test_confirmed_gate_expires_after_valid_until() -> None:
    registry = GateRegistry()
    registry.record_decision(
        GateDecision(
            gate_id="G-02",
            status=GateStatus.CONFIRMED,
            evidence_url="https://example.com/kis-probe",
            confirmed_at_utc=_NOW,
            valid_until_utc=_NOW + 1000,
            recorded_at_utc=_NOW,
        )
    )

    assert registry.effective_status("G-02", _NOW + 500) == GateStatus.CONFIRMED
    assert registry.effective_status("G-02", _NOW + 1000) == GateStatus.EXPIRED
    assert registry.blocks("G-02", _NOW + 1000) is True


def test_rejected_gate_always_blocks() -> None:
    registry = GateRegistry()
    registry.record_decision(
        GateDecision(gate_id="G-01", status=GateStatus.REJECTED, recorded_at_utc=_NOW)
    )
    assert registry.blocks("G-01", _NOW) is True


def test_effective_status_for_unknown_gate_id_raises() -> None:
    registry = GateRegistry()
    with pytest.raises(UnknownGateError):
        registry.effective_status("G-99", _NOW)
