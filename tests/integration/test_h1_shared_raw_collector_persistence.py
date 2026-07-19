"""H1 공유 수집기와 PostgreSQL catalog·gzip append-only 저장 통합 검증."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from skhy_research.adapters.persistence.raw_recorder import RawRecorder
from skhy_research.application.h1_shared_raw_collector import (
    H1SharedRawCollector,
    build_kis_h1_stream_catalog,
)
from tests._h1_shared_stream_support import load_h1_shared_fixture


@pytest.mark.integration
def test_fixture_capture_is_append_only_and_lineage_addressable(clean_pg, tmp_path: Path) -> None:
    trading_date, packets = load_h1_shared_fixture()
    recorder = RawRecorder(clean_pg, tmp_path)
    collector = H1SharedRawCollector(
        recorder=recorder,
        provider_catalog=build_kis_h1_stream_catalog(
            last_verified_at_utc=packets[0].received_time_utc
        ),
        trading_date=trading_date,
        collection_run_id="h1-shared-persistence-fixture",
    )

    summary = collector.store_packets(packets)

    assert len(summary.raw_record_ids) == len(packets)
    assert len(list((tmp_path / "raw" / "kis").rglob("*.json.gz"))) == len(packets)
    metas = {}
    for raw_record_id in summary.raw_record_ids:
        meta = recorder.get_meta(raw_record_id)
        assert meta is not None
        metas[raw_record_id] = meta
        assert meta.collection_run_id == "h1-shared-persistence-fixture"
        assert meta.provider_catalog_version == "kis-h1-public-websocket-v1"
        assert meta.license_terms.storage_redistribution_allowed is False

    close_id = next(
        raw_id
        for raw_id in summary.raw_record_ids
        if metas[raw_id].dataset == "h1_krx_close_indicative_raw_v1"
    )
    close_meta = metas[close_id]
    with gzip.open(close_meta.storage_path, "rb") as fh:
        close_payload = json.load(fh)
    assert close_payload["indicative"]["antc_cnpr"] == "281000"
    assert close_payload["indicative"]["antc_vol"] == "125000"

    repeated = collector.store_packet(packets[-1])
    assert repeated.was_duplicate is True
    assert repeated.raw_record_id == close_id
    assert len(list((tmp_path / "raw" / "kis").rglob("*.json.gz"))) == len(packets)
