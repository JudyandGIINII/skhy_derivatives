"""P0-10 검증: 공급자별 건강상태 추적과 장애 격리 (PRD 13.2)."""

from __future__ import annotations

from skhy_research.application.health_monitor import HealthMonitor

_NOW = 1_800_000_000_000_000_000


def test_record_event_marks_connected_with_latency() -> None:
    monitor = HealthMonitor()
    monitor.record_event("market_data", "kis", event_time_utc=_NOW, latency_ms=120.0)

    state = monitor.snapshot()[("market_data", "kis")]
    assert state.is_connected is True
    assert state.last_event_at_utc == _NOW
    assert state.measured_latency_ms == 120.0
    assert state.consecutive_failures == 0


def test_record_failure_increments_consecutive_failures_and_disconnects() -> None:
    monitor = HealthMonitor()
    monitor.record_event("market_data", "kis", event_time_utc=_NOW, latency_ms=100.0)
    monitor.record_failure("market_data", "kis", "timeout")
    monitor.record_failure("market_data", "kis", "timeout again")

    state = monitor.snapshot()[("market_data", "kis")]
    assert state.is_connected is False
    assert state.consecutive_failures == 2
    assert state.last_error == "timeout again"


def test_record_reconnect_resets_failure_streak() -> None:
    monitor = HealthMonitor()
    monitor.record_failure("market_data", "kis", "timeout")
    monitor.record_reconnect("market_data", "kis")

    state = monitor.snapshot()[("market_data", "kis")]
    assert state.is_connected is True
    assert state.consecutive_failures == 0
    assert state.reconnect_count == 1


def test_record_backfill_tracks_gap_and_marks_supplemented() -> None:
    monitor = HealthMonitor()
    monitor.record_backfill("market_data", "kis", gap_start_utc=_NOW, gap_end_utc=_NOW + 5_000_000_000)

    state = monitor.snapshot()[("market_data", "kis")]
    assert state.backfilled_gaps == [(_NOW, _NOW + 5_000_000_000)]
    assert state.to_dict()["backfilled_gap_count"] == 1


def test_one_provider_failure_does_not_report_others_as_failed() -> None:
    """한 공급자의 장애가 다른 공급자 상태를 오염시키지 않는다 (PRD 13.2 장애 격리)."""
    monitor = HealthMonitor()
    monitor.record_event("market_data", "kis", event_time_utc=_NOW, latency_ms=100.0)
    monitor.record_event("market_data", "toss", event_time_utc=_NOW, latency_ms=150.0)
    monitor.record_failure("market_data", "kis", "connection reset")

    assert monitor.is_isolated_failure(("market_data", "kis")) is True  # kis만 죽고 나머지는 정상


def test_multiple_provider_failures_are_not_reported_as_isolated() -> None:
    monitor = HealthMonitor()
    monitor.record_event("market_data", "kis", event_time_utc=_NOW, latency_ms=100.0)
    monitor.record_failure("market_data", "kis", "down")
    monitor.record_failure("market_data", "toss", "also down")

    assert monitor.is_isolated_failure(("market_data", "kis")) is False
