"""H1 공유 WebSocket sanitized fixture 조립 지원."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import cast

from skhy_research.adapters.persistence.raw_recorder import RawStoreOutcome, compute_checksum
from skhy_research.adapters.providers.kis.h1_websocket import (
    KisH1WebSocketPacket,
    decode_h1_data_frame,
    h1_feed_spec,
)
from skhy_research.domain.provider_capability import ProviderCatalogEntry
from skhy_research.domain.raw_record import RawRecordMeta

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "kis" / "h1_shared_stream_sanitized.json"


def load_h1_shared_fixture() -> tuple[date, tuple[KisH1WebSocketPacket, ...]]:
    fixture = cast(dict[str, object], json.loads(_FIXTURE_PATH.read_text(encoding="utf-8")))
    trading_date = date.fromisoformat(cast(str, fixture["trading_date"]))
    records = cast(list[dict[str, object]], fixture["records"])
    packets: list[KisH1WebSocketPacket] = []
    for record in records:
        tr_id = cast(str, record["tr_id"])
        spec = h1_feed_spec(tr_id)
        overrides = cast(dict[str, str], record["values"])
        values = tuple(overrides.get(field, "0") for field in spec.fields)
        raw_frame = f"0|{tr_id}|1|{'^'.join(values)}"
        received_at = datetime.fromisoformat(cast(str, record["received_time_kst"]))
        received_time_utc = int(received_at.timestamp() * 1_000_000_000)
        packets.extend(
            decode_h1_data_frame(
                raw_frame,
                received_time_utc=received_time_utc,
                provider_sequence=cast(str, record["provider_sequence"]),
            )
        )
    return trading_date, tuple(packets)


class MemoryRawRecorder:
    """collector unit test용 append-only recorder double."""

    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}
        self.datasets: dict[str, str] = {}
        self._by_dedupe_key: dict[tuple[str, str, str], RawRecordMeta] = {}

    def store(
        self,
        source: str,
        dataset: str,
        payload: bytes,
        received_at_utc: int,
        collection_run_id: str,
        dedupe_key: str,
        provider_catalog: ProviderCatalogEntry,
        provider_sequence: str | None = None,
    ) -> RawStoreOutcome:
        key = (source, dataset, dedupe_key)
        checksum = compute_checksum(payload)
        existing = self._by_dedupe_key.get(key)
        if existing is not None:
            return RawStoreOutcome(
                meta=existing,
                was_duplicate=existing.payload_checksum == checksum,
                was_conflict=existing.payload_checksum != checksum,
            )
        raw_record_id = f"raw-{len(self._by_dedupe_key) + 1}"
        meta = RawRecordMeta(
            raw_record_id=raw_record_id,
            source=source,
            dataset=dataset,
            dedupe_key=dedupe_key,
            payload_checksum=checksum,
            received_at_utc=received_at_utc,
            collection_run_id=collection_run_id,
            license_terms=provider_catalog.license_terms_snapshot(),
            provider_catalog_version=provider_catalog.catalog_version,
            provider_sequence=provider_sequence,
            storage_path=f"memory://{raw_record_id}",
            conflict_with=None,
        )
        self._by_dedupe_key[key] = meta
        self.payloads[raw_record_id] = payload
        self.datasets[raw_record_id] = dataset
        return RawStoreOutcome(meta=meta, was_duplicate=False, was_conflict=False)
