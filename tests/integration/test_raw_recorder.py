"""P0-08 통합 검증: 불변 저장, dedupe, 충돌 보존, 체크포인트 재개."""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest

from skhy_research.adapters.persistence.raw_recorder import (
    RawRecordCorruptionError,
    RawRecorder,
    compute_dedupe_key,
)

_NOW = time.time_ns()


def _recorder(clean_pg, tmp_path: Path) -> RawRecorder:
    return RawRecorder(clean_pg, tmp_path)


@pytest.mark.integration
def test_store_new_record_creates_file_and_catalog_row(clean_pg, tmp_path: Path) -> None:
    recorder = _recorder(clean_pg, tmp_path)
    payload = json.dumps({"symbol": "000660", "close": 203000}).encode("utf-8")
    dedupe_key = compute_dedupe_key("krx", "daily_ohlcv", "bar", _NOW, "irrelevant")

    outcome = recorder.store(
        source="krx",
        dataset="daily_ohlcv",
        payload=payload,
        received_at_utc=_NOW,
        collection_run_id="run-1",
        dedupe_key=dedupe_key,
    )

    assert outcome.was_duplicate is False
    assert outcome.was_conflict is False
    stored_path = Path(outcome.meta.storage_path)
    assert stored_path.exists()
    with gzip.open(stored_path, "rb") as fh:
        assert fh.read() == payload
    assert "krx" in str(stored_path)
    assert "daily_ohlcv" in str(stored_path)


@pytest.mark.integration
def test_reingesting_identical_payload_is_idempotent(clean_pg, tmp_path: Path) -> None:
    recorder = _recorder(clean_pg, tmp_path)
    payload = json.dumps({"symbol": "000660", "close": 203000}).encode("utf-8")
    dedupe_key = compute_dedupe_key("krx", "daily_ohlcv", "bar", _NOW, "irrelevant")

    first = recorder.store(
        source="krx",
        dataset="daily_ohlcv",
        payload=payload,
        received_at_utc=_NOW,
        collection_run_id="run-1",
        dedupe_key=dedupe_key,
    )
    second = recorder.store(
        source="krx",
        dataset="daily_ohlcv",
        payload=payload,
        received_at_utc=_NOW,
        collection_run_id="run-2",  # 재시작 후 다른 run_id로 재수집을 시도해도
        dedupe_key=dedupe_key,
    )

    assert second.was_duplicate is True
    assert second.meta.raw_record_id == first.meta.raw_record_id

    raw_dir = tmp_path / "raw" / "krx" / "daily_ohlcv"
    all_files = list(raw_dir.rglob("*.json.gz"))
    assert len(all_files) == 1  # 새 파일이 생기지 않았다


@pytest.mark.integration
def test_conflicting_payload_with_same_dedupe_key_is_preserved_not_dropped(
    clean_pg, tmp_path: Path
) -> None:
    recorder = _recorder(clean_pg, tmp_path)
    dedupe_key = compute_dedupe_key("krx", "daily_ohlcv", "bar", _NOW, "irrelevant")
    original = recorder.store(
        source="krx",
        dataset="daily_ohlcv",
        payload=json.dumps({"close": 203000}).encode("utf-8"),
        received_at_utc=_NOW,
        collection_run_id="run-1",
        dedupe_key=dedupe_key,
    )
    conflicting = recorder.store(
        source="krx",
        dataset="daily_ohlcv",
        payload=json.dumps({"close": 999999}).encode("utf-8"),  # 같은 key, 다른 내용
        received_at_utc=_NOW,
        collection_run_id="run-1",
        dedupe_key=dedupe_key,
    )

    assert conflicting.was_conflict is True
    assert conflicting.meta.raw_record_id != original.meta.raw_record_id
    assert conflicting.meta.conflict_with == original.meta.raw_record_id

    # 원본 파일은 그대로 보존된다
    with gzip.open(Path(original.meta.storage_path), "rb") as fh:
        assert json.loads(fh.read()) == {"close": 203000}
    with gzip.open(Path(conflicting.meta.storage_path), "rb") as fh:
        assert json.loads(fh.read()) == {"close": 999999}


@pytest.mark.integration
def test_tampered_file_is_detected_as_corruption_on_reingest(clean_pg, tmp_path: Path) -> None:
    recorder = _recorder(clean_pg, tmp_path)
    dedupe_key = compute_dedupe_key("krx", "daily_ohlcv", "bar", _NOW, "irrelevant")
    payload = json.dumps({"close": 203000}).encode("utf-8")
    stored = recorder.store(
        source="krx",
        dataset="daily_ohlcv",
        payload=payload,
        received_at_utc=_NOW,
        collection_run_id="run-1",
        dedupe_key=dedupe_key,
    )

    # 저장된 원시 파일을 직접 손상시킨다 (디스크 오류·수동 편집 등을 시뮬레이션)
    tampered_path = Path(stored.meta.storage_path)
    with gzip.open(tampered_path, "wb") as fh:
        fh.write(b'{"close": -1}')

    with pytest.raises(RawRecordCorruptionError):
        recorder.store(
            source="krx",
            dataset="daily_ohlcv",
            payload=payload,  # catalog에 남은 checksum과는 일치하지만 파일 내용은 다르다
            received_at_utc=_NOW,
            collection_run_id="run-2",
            dedupe_key=dedupe_key,
        )


@pytest.mark.integration
def test_checkpoint_round_trip_and_advance(clean_pg, tmp_path: Path) -> None:
    recorder = _recorder(clean_pg, tmp_path)
    assert recorder.get_checkpoint("kis", "quotes") is None

    recorder.advance_checkpoint("kis", "quotes", cursor="seq-100", updated_at_utc=_NOW)
    assert recorder.get_checkpoint("kis", "quotes") == "seq-100"

    recorder.advance_checkpoint("kis", "quotes", cursor="seq-200", updated_at_utc=_NOW + 1)
    assert recorder.get_checkpoint("kis", "quotes") == "seq-200"


@pytest.mark.integration
def test_checkpoints_are_isolated_per_source_and_dataset(clean_pg, tmp_path: Path) -> None:
    recorder = _recorder(clean_pg, tmp_path)
    recorder.advance_checkpoint("kis", "quotes", cursor="A", updated_at_utc=_NOW)
    recorder.advance_checkpoint("toss", "quotes", cursor="B", updated_at_utc=_NOW)
    recorder.advance_checkpoint("kis", "trades", cursor="C", updated_at_utc=_NOW)

    assert recorder.get_checkpoint("kis", "quotes") == "A"
    assert recorder.get_checkpoint("toss", "quotes") == "B"
    assert recorder.get_checkpoint("kis", "trades") == "C"
