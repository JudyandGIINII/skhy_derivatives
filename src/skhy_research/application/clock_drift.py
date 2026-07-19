"""로컬 시계 오차 측정 (PRD 13.4: "수신지연과 시계 오차를 측정해 모든 레코드에 남긴다")."""

from __future__ import annotations


def measure_clock_drift_ns(local_utc_ns: int, reference_utc_ns: int) -> int:
    """local이 reference보다 빠르면 양수, 느리면 음수."""
    return local_utc_ns - reference_utc_ns


def exceeds_drift_threshold(drift_ns: int, max_drift_ns: int) -> bool:
    return abs(drift_ns) > max_drift_ns
