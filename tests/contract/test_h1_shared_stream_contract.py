"""H1 KIS 공개 WebSocket allowlist·ordered schema·sanitized frame 계약."""

from __future__ import annotations

import json

import pytest

from skhy_research.adapters.providers.kis.h1_websocket import (
    H1_SHARED_CAPTURE_SYMBOL,
    KisH1WireError,
    build_h1_subscription_messages,
    decode_h1_data_frame,
    h1_feed_spec,
    h1_subscription_tr_ids,
)
from tests._h1_shared_stream_support import load_h1_shared_fixture

_EXPECTED_SCHEMAS = {
    "H0STASP0": (59, "85c20cc6c711d878077e3be5841a9c717d6be2e2c7f2ee6879b5dd1aa81a3114"),
    "H0STPGM0": (11, "b375ca82bd7f9fed02376e5d761d9c8bf5f316c0ceda67196664cffa959739bf"),
    "H0UNPGM0": (11, "b375ca82bd7f9fed02376e5d761d9c8bf5f316c0ceda67196664cffa959739bf"),
    "H0NXPGM0": (11, "b375ca82bd7f9fed02376e5d761d9c8bf5f316c0ceda67196664cffa959739bf"),
    "H0STCNT0": (46, "f767999430f5a1fd64a0ca31a211ae3e0b7430ef657644e605517e7b12e4c3c2"),
}


@pytest.mark.contract
def test_read_only_subscription_allowlist_contains_only_sealed_public_feeds() -> None:
    messages = build_h1_subscription_messages("sanitized-approval-key")

    assert h1_subscription_tr_ids() == tuple(_EXPECTED_SCHEMAS)
    assert len(messages) == 5
    decoded = [json.loads(message) for message in messages]
    assert {item["body"]["input"]["tr_id"] for item in decoded} == set(_EXPECTED_SCHEMAS)
    assert {item["body"]["input"]["tr_key"] for item in decoded} == {
        H1_SHARED_CAPTURE_SYMBOL
    }
    assert {item["header"]["tr_type"] for item in decoded} == {"1"}
    assert all("account" not in message.lower() and "order" not in message.lower() for message in messages)


@pytest.mark.contract
def test_ordered_schema_hashes_are_sealed_to_kis_official_field_order() -> None:
    actual = {
        tr_id: (len(h1_feed_spec(tr_id).fields), h1_feed_spec(tr_id).schema_hash)
        for tr_id in h1_subscription_tr_ids()
    }
    assert actual == _EXPECTED_SCHEMAS
    order_book_fields = h1_feed_spec("H0STASP0").fields
    assert tuple(
        field for field in order_book_fields if field in {"ANTC_CNPR", "ANTC_CNQN", "ANTC_VOL"}
    ) == ("ANTC_CNPR", "ANTC_CNQN", "ANTC_VOL")


@pytest.mark.contract
def test_sanitized_frames_cover_primary_and_diagnostic_feeds() -> None:
    _, packets = load_h1_shared_fixture()

    assert {packet.tr_id for packet in packets} == set(_EXPECTED_SCHEMAS)
    assert all(packet.symbol == H1_SHARED_CAPTURE_SYMBOL for packet in packets)
    close_packet = next(packet for packet in packets if packet.provider_time_text == "152001")
    assert close_packet.data["ANTC_CNPR"] == "281000"
    assert close_packet.data["ANTC_CNQN"] == "7000"
    assert close_packet.data["ANTC_VOL"] == "125000"


@pytest.mark.contract
def test_schema_drift_and_non_public_frames_fail_closed() -> None:
    with pytest.raises(KisH1WireError, match="schema drift"):
        decode_h1_data_frame(
            "0|H0STPGM0|1|000660^150500",
            received_time_utc=1,
        )
    with pytest.raises(KisH1WireError, match="암호화"):
        decode_h1_data_frame(
            "1|H0STPGM0|1|encrypted",
            received_time_utc=1,
        )
    with pytest.raises(KisH1WireError, match="allowlist"):
        decode_h1_data_frame(
            "0|H0STCNI0|1|private-order-notice",
            received_time_utc=1,
        )


@pytest.mark.contract
def test_multi_record_frame_preserves_source_frame_and_splits_lineage_sequence() -> None:
    spec = h1_feed_spec("H0STPGM0")
    first = {field: "0" for field in spec.fields}
    second = dict(first)
    first.update({"MKSC_SHRN_ISCD": "000660", "STCK_CNTG_HOUR": "150001"})
    second.update({"MKSC_SHRN_ISCD": "000660", "STCK_CNTG_HOUR": "150002"})
    payload = "^".join(first[field] for field in spec.fields)
    payload += "^" + "^".join(second[field] for field in spec.fields)
    raw_frame = f"0|H0STPGM0|2|{payload}"

    packets = decode_h1_data_frame(
        raw_frame,
        received_time_utc=1,
        provider_sequence="batch-7",
    )

    assert len(packets) == 2
    assert all(packet.raw_frame == raw_frame for packet in packets)
    assert [packet.provider_sequence for packet in packets] == ["batch-7:0", "batch-7:1"]
    assert [packet.provider_time_text for packet in packets] == ["150001", "150002"]
