"""P1-05 검증: 이벤트 정렬 규칙이 버전을 가지며 다단계 키로 결정론적이다."""

from __future__ import annotations

from skhy_research.domain.simulation_event import ORDERING_VERSION, SimulationEvent, sort_events

_T0 = 1_800_000_000_000_000_000


def test_ordering_version_is_declared() -> None:
    assert ORDERING_VERSION


def test_primary_sort_key_is_available_time() -> None:
    events = [
        SimulationEvent("e2", available_time_utc=_T0 + 2, event_time_utc=_T0, venue="KRX", event_type="quote", provider_sequence=None, payload=None),
        SimulationEvent("e1", available_time_utc=_T0 + 1, event_time_utc=_T0 + 5, venue="KRX", event_type="quote", provider_sequence=None, payload=None),
    ]
    ordered = sort_events(events)
    assert [e.event_id for e in ordered] == ["e1", "e2"]


def test_tie_on_available_time_breaks_by_event_time() -> None:
    events = [
        SimulationEvent("late", available_time_utc=_T0, event_time_utc=_T0 + 10, venue="KRX", event_type="quote", provider_sequence=None, payload=None),
        SimulationEvent("early", available_time_utc=_T0, event_time_utc=_T0 + 1, venue="KRX", event_type="quote", provider_sequence=None, payload=None),
    ]
    ordered = sort_events(events)
    assert [e.event_id for e in ordered] == ["early", "late"]


def test_tie_on_time_breaks_by_provider_sequence() -> None:
    events = [
        SimulationEvent("seq2", available_time_utc=_T0, event_time_utc=_T0, venue="KRX", event_type="quote", provider_sequence=2, payload=None),
        SimulationEvent("seq1", available_time_utc=_T0, event_time_utc=_T0, venue="KRX", event_type="quote", provider_sequence=1, payload=None),
        SimulationEvent("no_seq", available_time_utc=_T0, event_time_utc=_T0, venue="KRX", event_type="quote", provider_sequence=None, payload=None),
    ]
    ordered = sort_events(events)
    assert [e.event_id for e in ordered] == ["seq1", "seq2", "no_seq"]  # sequence 없으면 항상 뒤로


def test_tie_on_sequence_breaks_by_venue_priority() -> None:
    events = [
        SimulationEvent("hkex", available_time_utc=_T0, event_time_utc=_T0, venue="HKEX", event_type="quote", provider_sequence=None, payload=None),
        SimulationEvent("krx", available_time_utc=_T0, event_time_utc=_T0, venue="KRX", event_type="quote", provider_sequence=None, payload=None),
        SimulationEvent("nasdaq", available_time_utc=_T0, event_time_utc=_T0, venue="NASDAQ", event_type="quote", provider_sequence=None, payload=None),
    ]
    ordered = sort_events(events)
    assert [e.event_id for e in ordered] == ["krx", "nasdaq", "hkex"]


def test_tie_on_everything_breaks_by_event_id_for_stability() -> None:
    events = [
        SimulationEvent("z", available_time_utc=_T0, event_time_utc=_T0, venue="KRX", event_type="quote", provider_sequence=None, payload=None),
        SimulationEvent("a", available_time_utc=_T0, event_time_utc=_T0, venue="KRX", event_type="quote", provider_sequence=None, payload=None),
    ]
    ordered = sort_events(events)
    assert [e.event_id for e in ordered] == ["a", "z"]


def test_sort_is_deterministic_across_repeated_calls() -> None:
    events = [
        SimulationEvent(f"e{i}", available_time_utc=_T0 + (i % 3), event_time_utc=_T0 + i, venue="KRX", event_type="quote", provider_sequence=None, payload=None)
        for i in range(20)
    ]
    first = [e.event_id for e in sort_events(events)]
    second = [e.event_id for e in sort_events(events)]
    assert first == second
