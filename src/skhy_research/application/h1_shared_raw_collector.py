"""H1 연속장 proxy·향후 종가경매 모델이 공유하는 KIS raw 수집기.

허용된 공개 시세 packet을 14:59:50~15:30:10 KST에만 받아 기존 `RawRecorder`로
즉시 append-only 저장한다. feature·신호·회귀·broker는 이 모듈의 책임이 아니다.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, time
from typing import Protocol

from skhy_research.adapters.persistence.raw_recorder import (
    RawStoreOutcome,
    compute_dedupe_key,
)
from skhy_research.adapters.providers.kis.h1_websocket import (
    H1_SHARED_CAPTURE_SYMBOL,
    KisH1FeedRole,
    KisH1WebSocketPacket,
    decode_h1_data_frame,
)
from skhy_research.domain.calendar import (
    local_datetime_to_utc_nanos,
    utc_nanos_to_local_datetime,
)
from skhy_research.domain.enums import Venue
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)

H1_SHARED_RAW_FORMAT_VERSION = "kis_h1_shared_raw@1.0.0"
H1_CAPTURE_START_KST = time(14, 59, 50)
H1_CAPTURE_END_KST = time(15, 30, 10)
H1_CLOSE_AUCTION_START_KST = time(15, 20)
H1_CLOSE_AUCTION_END_KST = time(15, 30)

_DATASET_ORDER_BOOK = "h1_krx_orderbook_raw_v1"
_DATASET_CLOSE_INDICATIVE = "h1_krx_close_indicative_raw_v1"
_DATASET_PROGRAM_KRX = "h1_krx_program_raw_v1"
_DATASET_PROGRAM_INTEGRATED = "h1_integrated_program_raw_v1"
_DATASET_PROGRAM_NXT = "h1_nxt_program_diagnostic_raw_v1"
_DATASET_TRADE_DIAGNOSTIC = "h1_krx_trade_diagnostic_raw_v1"


class H1SharedRawCollectorError(ValueError):
    """세션·symbol·시각·provider catalog 계약 위반."""


class H1RawPersistenceConflictError(RuntimeError):
    """동일 provider sequence가 다른 payload를 가리키는 원시 충돌."""


class _RawRecorder(Protocol):
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
    ) -> RawStoreOutcome: ...


@dataclass(frozen=True)
class H1CaptureWindow:
    trading_date: date
    start_utc: int
    end_utc: int


@dataclass(frozen=True)
class H1StoredRawRecord:
    raw_record_id: str
    tr_id: str
    dataset: str
    schema_hash: str
    provider_event_time_utc: int
    received_time_utc: int
    venue: str
    record_class: str
    is_close_auction_indicative: bool
    was_duplicate: bool


@dataclass(frozen=True)
class H1CaptureSummary:
    collection_run_id: str
    raw_record_ids: tuple[str, ...]
    dataset_counts: dict[str, int]
    duplicate_count: int
    close_indicative_count: int


def build_h1_capture_window(trading_date: date) -> H1CaptureWindow:
    return H1CaptureWindow(
        trading_date=trading_date,
        start_utc=local_datetime_to_utc_nanos(trading_date, H1_CAPTURE_START_KST, Venue.KRX),
        end_utc=local_datetime_to_utc_nanos(trading_date, H1_CAPTURE_END_KST, Venue.KRX),
    )


def build_kis_h1_stream_catalog(*, last_verified_at_utc: int) -> ProviderCatalogEntry:
    """raw lineage에 동결할 KIS 공개 WebSocket capability snapshot."""

    return ProviderCatalogEntry(
        provider_name="kis",
        port_type="market_data",
        catalog_version="kis-h1-public-websocket-v1",
        capabilities=frozenset(
            {
                ProviderCapability.QUOTE_STREAM,
                ProviderCapability.TRADE_STREAM,
                ProviderCapability.EXPECTED_CLOSING_PRICE,
            }
        ),
        license_terms_url="https://apiportal.koreainvestment.com/intro",
        storage_redistribution_allowed=False,
        last_verified_at_utc=last_verified_at_utc,
        health_status=HealthStatus.UNKNOWN,
    )


class H1SharedRawCollector:
    """wire packet 하나를 검증·봉인해 append-only raw parent로 저장한다."""

    def __init__(
        self,
        *,
        recorder: _RawRecorder,
        provider_catalog: ProviderCatalogEntry,
        trading_date: date,
        collection_run_id: str,
    ) -> None:
        if not collection_run_id.strip():
            raise H1SharedRawCollectorError("collection_run_id가 비었다")
        if provider_catalog.provider_name != "kis":
            raise H1SharedRawCollectorError("H1 공유 수집 provider는 kis여야 한다")
        required = {
            ProviderCapability.QUOTE_STREAM,
            ProviderCapability.TRADE_STREAM,
            ProviderCapability.EXPECTED_CLOSING_PRICE,
        }
        if not required.issubset(provider_catalog.capabilities):
            missing = sorted(item.value for item in required - provider_catalog.capabilities)
            raise H1SharedRawCollectorError(f"KIS H1 stream capability 누락: {missing}")
        self._recorder = recorder
        self._provider_catalog = provider_catalog
        self._window = build_h1_capture_window(trading_date)
        self._collection_run_id = collection_run_id

    @property
    def window(self) -> H1CaptureWindow:
        return self._window

    def store_packet(self, packet: KisH1WebSocketPacket) -> H1StoredRawRecord:
        if packet.symbol != H1_SHARED_CAPTURE_SYMBOL:
            raise H1SharedRawCollectorError(
                f"H1 공유 수집 symbol은 {H1_SHARED_CAPTURE_SYMBOL}만 허용한다: {packet.symbol}"
            )
        provider_event_time_utc = _provider_event_time_utc(
            self._window.trading_date, packet.provider_time_text
        )
        self._assert_capture_window(provider_event_time_utc, packet.received_time_utc)

        dataset, record_class, is_close_indicative = _classify_packet(
            packet, provider_event_time_utc
        )
        payload = _canonical_raw_envelope(
            packet,
            provider_event_time_utc=provider_event_time_utc,
            dataset=dataset,
            record_class=record_class,
            is_close_indicative=is_close_indicative,
        )
        wire_checksum = hashlib.sha256(packet.record_frame.encode("utf-8")).hexdigest()
        dedupe_key = (
            f"{packet.tr_id}:{packet.symbol}:seq:{packet.provider_sequence}"
            if packet.provider_sequence is not None
            else compute_dedupe_key(
                "kis",
                dataset,
                packet.tr_id,
                provider_event_time_utc,
                wire_checksum,
            )
        )
        outcome = self._recorder.store(
            source="kis",
            dataset=dataset,
            payload=payload,
            received_at_utc=packet.received_time_utc,
            collection_run_id=self._collection_run_id,
            dedupe_key=dedupe_key,
            provider_catalog=self._provider_catalog,
            provider_sequence=packet.provider_sequence,
        )
        if outcome.was_conflict:
            raise H1RawPersistenceConflictError(
                f"{packet.tr_id} provider sequence의 raw payload checksum이 충돌했다"
            )
        return H1StoredRawRecord(
            raw_record_id=outcome.meta.raw_record_id,
            tr_id=packet.tr_id,
            dataset=dataset,
            schema_hash=packet.spec.schema_hash,
            provider_event_time_utc=provider_event_time_utc,
            received_time_utc=packet.received_time_utc,
            venue=packet.spec.venue,
            record_class=record_class,
            is_close_auction_indicative=is_close_indicative,
            was_duplicate=outcome.was_duplicate,
        )

    def store_packets(self, packets: Iterable[KisH1WebSocketPacket]) -> H1CaptureSummary:
        records = tuple(self.store_packet(packet) for packet in packets)
        counts = Counter(record.dataset for record in records)
        return H1CaptureSummary(
            collection_run_id=self._collection_run_id,
            raw_record_ids=tuple(record.raw_record_id for record in records),
            dataset_counts=dict(sorted(counts.items())),
            duplicate_count=sum(record.was_duplicate for record in records),
            close_indicative_count=sum(
                record.is_close_auction_indicative for record in records
            ),
        )

    def store_frame(
        self,
        raw_frame: str,
        *,
        received_time_utc: int,
        provider_sequence: str | None = None,
    ) -> H1CaptureSummary:
        """단일 KIS wire frame의 모든 record를 분리해 각각 raw parent로 저장한다."""

        packets = decode_h1_data_frame(
            raw_frame,
            received_time_utc=received_time_utc,
            provider_sequence=provider_sequence,
        )
        return self.store_packets(packets)

    def _assert_capture_window(self, provider_event_time_utc: int, received_time_utc: int) -> None:
        if not self._window.start_utc <= provider_event_time_utc <= self._window.end_utc:
            raise H1SharedRawCollectorError("provider event가 H1 공유 수집 window 밖이다")
        if not self._window.start_utc <= received_time_utc <= self._window.end_utc:
            raise H1SharedRawCollectorError("received time이 H1 공유 수집 window 밖이다")
        if received_time_utc < provider_event_time_utc:
            raise H1SharedRawCollectorError("received time이 provider event time보다 이르다")


def _provider_event_time_utc(trading_date: date, value: str) -> int:
    if len(value) != 6 or not value.isdigit():
        raise H1SharedRawCollectorError("KIS provider event time은 HHMMSS 6자리여야 한다")
    try:
        local_time = time(int(value[:2]), int(value[2:4]), int(value[4:6]))
    except ValueError as exc:
        raise H1SharedRawCollectorError(f"잘못된 KIS provider event time: {value}") from exc
    return local_datetime_to_utc_nanos(trading_date, local_time, Venue.KRX)


def _classify_packet(
    packet: KisH1WebSocketPacket, provider_event_time_utc: int
) -> tuple[str, str, bool]:
    local_time = utc_nanos_to_local_datetime(provider_event_time_utc, Venue.KRX).time()
    if packet.spec.role is KisH1FeedRole.ORDER_BOOK:
        is_close = H1_CLOSE_AUCTION_START_KST <= local_time <= H1_CLOSE_AUCTION_END_KST
        if is_close:
            return _DATASET_CLOSE_INDICATIVE, "KRX_CLOSE_INDICATIVE", True
        return _DATASET_ORDER_BOOK, "KRX_ORDER_BOOK", False
    if packet.spec.role is KisH1FeedRole.PROGRAM_KRX:
        return _DATASET_PROGRAM_KRX, "KRX_PROGRAM", False
    if packet.spec.role is KisH1FeedRole.PROGRAM_INTEGRATED_DIAGNOSTIC:
        return _DATASET_PROGRAM_INTEGRATED, "INTEGRATED_PROGRAM_DIAGNOSTIC", False
    if packet.spec.role is KisH1FeedRole.PROGRAM_NXT_DIAGNOSTIC:
        return _DATASET_PROGRAM_NXT, "NXT_PROGRAM_DIAGNOSTIC", False
    if packet.spec.role is KisH1FeedRole.TRADE_DIAGNOSTIC:
        return _DATASET_TRADE_DIAGNOSTIC, "KRX_TRADE_DIAGNOSTIC", False
    raise AssertionError(f"처리하지 않은 H1 feed role: {packet.spec.role}")


def _canonical_raw_envelope(
    packet: KisH1WebSocketPacket,
    *,
    provider_event_time_utc: int,
    dataset: str,
    record_class: str,
    is_close_indicative: bool,
) -> bytes:
    data = packet.data
    provider_event_kst = utc_nanos_to_local_datetime(provider_event_time_utc, Venue.KRX)
    received_kst = utc_nanos_to_local_datetime(packet.received_time_utc, Venue.KRX)
    envelope: dict[str, object] = {
        "raw_format_version": H1_SHARED_RAW_FORMAT_VERSION,
        "tr_id": packet.tr_id,
        "symbol": packet.symbol,
        "dataset": dataset,
        "venue": packet.spec.venue,
        "record_class": record_class,
        "schema_hash": packet.spec.schema_hash,
        "schema_fields": packet.fields,
        "provider_event_time_kst": provider_event_kst.isoformat(),
        "provider_event_time_utc": provider_event_time_utc,
        "received_time_kst": received_kst.isoformat(),
        "received_time_utc": packet.received_time_utc,
        "provider_sequence": packet.provider_sequence,
        "is_close_auction_indicative": is_close_indicative,
        "raw_frame": packet.raw_frame,
        "record_frame": packet.record_frame,
        "field_values": data,
    }
    if is_close_indicative:
        envelope["indicative"] = {
            "antc_cnpr": data["ANTC_CNPR"],
            "antc_cnqn": data["ANTC_CNQN"],
            "antc_vol": data["ANTC_VOL"],
        }
    return json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
