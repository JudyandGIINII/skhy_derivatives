"""H1 공유 raw 수집기의 세션·분류·lineage·fail-closed 단위 테스트."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from skhy_research.adapters.providers.kis.h1_websocket import (
    KisH1WebSocketPacket,
    decode_h1_data_frame,
    h1_feed_spec,
)
from skhy_research.application.h1_shared_raw_collector import (
    H1RawPersistenceConflictError,
    H1SharedRawCollector,
    H1SharedRawCollectorError,
    build_h1_capture_window,
    build_kis_h1_stream_catalog,
)
from skhy_research.domain.provider_capability import ProviderCapability
from tests._h1_shared_stream_support import MemoryRawRecorder, load_h1_shared_fixture


def _collector(
    recorder: MemoryRawRecorder,
) -> tuple[H1SharedRawCollector, tuple[KisH1WebSocketPacket, ...]]:
    trading_date, packets = load_h1_shared_fixture()
    catalog = build_kis_h1_stream_catalog(last_verified_at_utc=packets[0].received_time_utc)
    return (
        H1SharedRawCollector(
            recorder=recorder,
            provider_catalog=catalog,
            trading_date=trading_date,
            collection_run_id="h1-shared-fixture-run",
        ),
        packets,
    )


def test_fixture_packets_are_partitioned_and_close_indicative_is_first_class() -> None:
    recorder = MemoryRawRecorder()
    collector, packets = _collector(recorder)

    summary = collector.store_packets(packets)

    assert len(summary.raw_record_ids) == 6
    assert summary.duplicate_count == 0
    assert summary.close_indicative_count == 1
    assert summary.dataset_counts == {
        "h1_integrated_program_raw_v1": 1,
        "h1_krx_close_indicative_raw_v1": 1,
        "h1_krx_orderbook_raw_v1": 1,
        "h1_krx_program_raw_v1": 1,
        "h1_krx_trade_diagnostic_raw_v1": 1,
        "h1_nxt_program_diagnostic_raw_v1": 1,
    }
    close_raw_id = next(
        raw_id
        for raw_id, dataset in recorder.datasets.items()
        if dataset == "h1_krx_close_indicative_raw_v1"
    )
    envelope = json.loads(recorder.payloads[close_raw_id])
    assert envelope["tr_id"] == "H0STASP0"
    assert envelope["venue"] == "KRX"
    assert envelope["record_class"] == "KRX_CLOSE_INDICATIVE"
    assert envelope["is_close_auction_indicative"] is True
    assert envelope["indicative"] == {
        "antc_cnpr": "281000",
        "antc_cnqn": "7000",
        "antc_vol": "125000",
    }
    assert envelope["provider_event_time_kst"].startswith("2026-07-20T15:20:01")
    assert envelope["received_time_kst"].startswith("2026-07-20T15:20:01.075")
    assert len(envelope["schema_hash"]) == 64
    assert envelope["raw_frame"].startswith("0|H0STASP0|1|")


def test_same_provider_packet_is_idempotent_and_keeps_lineage_parent() -> None:
    recorder = MemoryRawRecorder()
    collector, packets = _collector(recorder)
    packet = packets[0]

    first_summary = collector.store_frame(
        packet.raw_frame,
        received_time_utc=packet.received_time_utc,
        provider_sequence=packet.provider_sequence,
    )
    second = collector.store_packet(packet)

    assert second.was_duplicate is True
    assert second.raw_record_id == first_summary.raw_record_ids[0]
    assert len(recorder.payloads) == 1


def test_same_provider_sequence_with_changed_payload_is_conflict() -> None:
    recorder = MemoryRawRecorder()
    collector, packets = _collector(recorder)
    packet = packets[1]
    collector.store_packet(packet)
    changed_values = list(packet.values)
    field_index = packet.fields.index("NTBY_TR_PBMN")
    changed_values[field_index] = "999"
    raw_frame = f"0|{packet.tr_id}|1|{'^'.join(changed_values)}"
    changed = decode_h1_data_frame(
        raw_frame,
        received_time_utc=packet.received_time_utc,
        provider_sequence=packet.provider_sequence,
    )[0]

    with pytest.raises(H1RawPersistenceConflictError, match="checksum"):
        collector.store_packet(changed)


def test_wrong_symbol_and_outside_window_fail_before_persistence() -> None:
    recorder = MemoryRawRecorder()
    collector, packets = _collector(recorder)
    packet = packets[0]
    fields = h1_feed_spec(packet.tr_id).fields
    values = list(packet.values)
    values[fields.index("MKSC_SHRN_ISCD")] = "005930"
    wrong_symbol = decode_h1_data_frame(
        f"0|{packet.tr_id}|1|{'^'.join(values)}",
        received_time_utc=packet.received_time_utc,
    )[0]

    with pytest.raises(H1SharedRawCollectorError, match="000660"):
        collector.store_packet(wrong_symbol)

    outside = replace(packet, received_time_utc=collector.window.end_utc + 1)
    with pytest.raises(H1SharedRawCollectorError, match="window"):
        collector.store_packet(outside)
    assert recorder.payloads == {}


def test_capture_window_is_exact_kst_and_catalog_is_read_only_stream_only() -> None:
    trading_date, packets = load_h1_shared_fixture()
    window = build_h1_capture_window(trading_date)
    expected_duration_ns = int(
        timedelta(minutes=30, seconds=20).total_seconds() * 1_000_000_000
    )
    assert window.end_utc - window.start_utc == expected_duration_ns

    catalog = build_kis_h1_stream_catalog(last_verified_at_utc=packets[0].received_time_utc)
    assert catalog.capabilities == frozenset(
        {
            ProviderCapability.QUOTE_STREAM,
            ProviderCapability.TRADE_STREAM,
            ProviderCapability.EXPECTED_CLOSING_PRICE,
        }
    )
    assert ProviderCapability.ORDER_SUBMIT not in catalog.capabilities
    assert catalog.storage_redistribution_allowed is False


def test_missing_stream_capability_is_rejected() -> None:
    trading_date, packets = load_h1_shared_fixture()
    catalog = build_kis_h1_stream_catalog(
        last_verified_at_utc=packets[0].received_time_utc
    ).model_copy(update={"capabilities": frozenset({ProviderCapability.QUOTE_STREAM})})

    with pytest.raises(H1SharedRawCollectorError, match="capability"):
        H1SharedRawCollector(
            recorder=MemoryRawRecorder(),
            provider_catalog=catalog,
            trading_date=trading_date,
            collection_run_id="missing-capability",
        )
