"""P0-10 검증: 시계 오차 측정과 임계값 판정."""

from __future__ import annotations

from skhy_research.application.clock_drift import exceeds_drift_threshold, measure_clock_drift_ns


def test_positive_drift_when_local_is_ahead() -> None:
    assert measure_clock_drift_ns(local_utc_ns=1_000_500, reference_utc_ns=1_000_000) == 500


def test_negative_drift_when_local_is_behind() -> None:
    assert measure_clock_drift_ns(local_utc_ns=999_500, reference_utc_ns=1_000_000) == -500


def test_exceeds_threshold_uses_absolute_value() -> None:
    assert exceeds_drift_threshold(drift_ns=-2_000_000_000, max_drift_ns=1_000_000_000) is True
    assert exceeds_drift_threshold(drift_ns=500_000_000, max_drift_ns=1_000_000_000) is False


def test_drift_exactly_at_threshold_does_not_exceed() -> None:
    assert exceeds_drift_threshold(drift_ns=1_000_000_000, max_drift_ns=1_000_000_000) is False
