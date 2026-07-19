"""H1 공유 raw 수집용 KIS 공개 WebSocket wire 계약.

계좌·주문 TR은 허용하지 않는다. 이 모듈은 KIS 공개 시세 frame을 schema에 따라
분해하고 read-only 구독 payload를 만드는 역할만 한다. 네트워크 연결·approval key
발급과 실제 장중 실행은 별도 runbook 단계다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

H1_SHARED_CAPTURE_SYMBOL = "000660"


class KisH1WireError(ValueError):
    """허용되지 않은 TR·암호화 frame·schema drift 등 wire 계약 위반."""


class KisH1FeedRole(StrEnum):
    ORDER_BOOK = "ORDER_BOOK"
    PROGRAM_KRX = "PROGRAM_KRX"
    PROGRAM_INTEGRATED_DIAGNOSTIC = "PROGRAM_INTEGRATED_DIAGNOSTIC"
    PROGRAM_NXT_DIAGNOSTIC = "PROGRAM_NXT_DIAGNOSTIC"
    TRADE_DIAGNOSTIC = "TRADE_DIAGNOSTIC"


@dataclass(frozen=True)
class KisH1FeedSpec:
    tr_id: str
    venue: str
    role: KisH1FeedRole
    event_time_field: str
    fields: tuple[str, ...]

    @property
    def schema_hash(self) -> str:
        encoded = json.dumps(self.fields, ensure_ascii=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


_ORDER_BOOK_FIELDS = (
    "MKSC_SHRN_ISCD",
    "BSOP_HOUR",
    "HOUR_CLS_CODE",
    *(f"ASKP{level}" for level in range(1, 11)),
    *(f"BIDP{level}" for level in range(1, 11)),
    *(f"ASKP_RSQN{level}" for level in range(1, 11)),
    *(f"BIDP_RSQN{level}" for level in range(1, 11)),
    "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN",
    "OVTM_TOTAL_ASKP_RSQN",
    "OVTM_TOTAL_BIDP_RSQN",
    "ANTC_CNPR",
    "ANTC_CNQN",
    "ANTC_VOL",
    "ANTC_CNTG_VRSS",
    "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT",
    "ACML_VOL",
    "TOTAL_ASKP_RSQN_ICDC",
    "TOTAL_BIDP_RSQN_ICDC",
    "OVTM_TOTAL_ASKP_ICDC",
    "OVTM_TOTAL_BIDP_ICDC",
    "STCK_DEAL_CLS_CODE",
)

_PROGRAM_FIELDS = (
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "SELN_CNQN",
    "SELN_TR_PBMN",
    "SHNU_CNQN",
    "SHNU_TR_PBMN",
    "NTBY_CNQN",
    "NTBY_TR_PBMN",
    "SELN_RSQN",
    "SHNU_RSQN",
    "WHOL_NTBY_QTY",
)

_TRADE_FIELDS = (
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "STCK_PRPR",
    "PRDY_VRSS_SIGN",
    "PRDY_VRSS",
    "PRDY_CTRT",
    "WGHN_AVRG_STCK_PRC",
    "STCK_OPRC",
    "STCK_HGPR",
    "STCK_LWPR",
    "ASKP1",
    "BIDP1",
    "CNTG_VOL",
    "ACML_VOL",
    "ACML_TR_PBMN",
    "SELN_CNTG_CSNU",
    "SHNU_CNTG_CSNU",
    "NTBY_CNTG_CSNU",
    "CTTR",
    "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN",
    "CCLD_DVSN",
    "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE",
    "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR",
    "HGPR_HOUR",
    "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_PRPR",
    "LWPR_HOUR",
    "LWPR_VRSS_PRPR_SIGN",
    "LWPR_VRSS_PRPR",
    "BSOP_DATE",
    "NEW_MKOP_CLS_CODE",
    "TRHT_YN",
    "ASKP_RSQN1",
    "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN",
    "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL",
    "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE",
    "MRKT_TRTM_CLS_CODE",
    "VI_STND_PRC",
)

_FEED_SPECS = {
    "H0STASP0": KisH1FeedSpec(
        tr_id="H0STASP0",
        venue="KRX",
        role=KisH1FeedRole.ORDER_BOOK,
        event_time_field="BSOP_HOUR",
        fields=_ORDER_BOOK_FIELDS,
    ),
    "H0STPGM0": KisH1FeedSpec(
        tr_id="H0STPGM0",
        venue="KRX",
        role=KisH1FeedRole.PROGRAM_KRX,
        event_time_field="STCK_CNTG_HOUR",
        fields=_PROGRAM_FIELDS,
    ),
    "H0UNPGM0": KisH1FeedSpec(
        tr_id="H0UNPGM0",
        venue="KRX_NXT_INTEGRATED",
        role=KisH1FeedRole.PROGRAM_INTEGRATED_DIAGNOSTIC,
        event_time_field="STCK_CNTG_HOUR",
        fields=_PROGRAM_FIELDS,
    ),
    "H0NXPGM0": KisH1FeedSpec(
        tr_id="H0NXPGM0",
        venue="NXT",
        role=KisH1FeedRole.PROGRAM_NXT_DIAGNOSTIC,
        event_time_field="STCK_CNTG_HOUR",
        fields=_PROGRAM_FIELDS,
    ),
    "H0STCNT0": KisH1FeedSpec(
        tr_id="H0STCNT0",
        venue="KRX",
        role=KisH1FeedRole.TRADE_DIAGNOSTIC,
        event_time_field="STCK_CNTG_HOUR",
        fields=_TRADE_FIELDS,
    ),
}


@dataclass(frozen=True)
class KisH1WebSocketPacket:
    """단일 KIS 공개 시세 record와 수신 lineage."""

    tr_id: str
    fields: tuple[str, ...]
    values: tuple[str, ...]
    raw_frame: str  # 공급자가 보낸 원본 frame 전체
    record_frame: str  # multi-record frame에서 분리한 단일 record canonical frame
    received_time_utc: int
    provider_sequence: str | None = None

    def __post_init__(self) -> None:
        spec = h1_feed_spec(self.tr_id)
        if self.fields != spec.fields:
            raise KisH1WireError(f"{self.tr_id} ordered schema가 봉인 계약과 다르다")
        if len(self.values) != len(self.fields):
            raise KisH1WireError(
                f"{self.tr_id} field/value 수가 다르다: {len(self.fields)} != {len(self.values)}"
            )
        if self.received_time_utc < 0:
            raise KisH1WireError("received_time_utc는 음수일 수 없다")
        expected_record_frame = f"0|{self.tr_id}|1|{'^'.join(self.values)}"
        if self.record_frame != expected_record_frame:
            raise KisH1WireError("decoded field와 단일 record frame이 일치하지 않는다")

    @property
    def spec(self) -> KisH1FeedSpec:
        return h1_feed_spec(self.tr_id)

    @property
    def data(self) -> dict[str, str]:
        return dict(zip(self.fields, self.values, strict=True))

    @property
    def symbol(self) -> str:
        return self.data["MKSC_SHRN_ISCD"]

    @property
    def provider_time_text(self) -> str:
        return self.data[self.spec.event_time_field]


def h1_feed_spec(tr_id: str) -> KisH1FeedSpec:
    try:
        return _FEED_SPECS[tr_id]
    except KeyError as exc:
        raise KisH1WireError(f"H1 read-only allowlist 밖 TR ID: {tr_id}") from exc


def h1_subscription_tr_ids() -> tuple[str, ...]:
    return tuple(_FEED_SPECS)


def build_h1_subscription_messages(
    approval_key: str,
    *,
    symbol: str = H1_SHARED_CAPTURE_SYMBOL,
    subscribe: bool = True,
) -> tuple[str, ...]:
    """KIS 공개 WebSocket 구독/해지 JSON만 생성한다."""

    if not approval_key.strip():
        raise KisH1WireError("WebSocket approval key가 비었다")
    if symbol != H1_SHARED_CAPTURE_SYMBOL:
        raise KisH1WireError(f"H1 공유 수집 symbol은 {H1_SHARED_CAPTURE_SYMBOL}만 허용한다")
    tr_type = "1" if subscribe else "2"
    messages = []
    for tr_id in h1_subscription_tr_ids():
        payload = {
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": tr_type,
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": tr_id, "tr_key": symbol}},
        }
        messages.append(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    return tuple(messages)


def decode_h1_data_frame(
    raw_frame: str,
    *,
    received_time_utc: int,
    provider_sequence: str | None = None,
) -> tuple[KisH1WebSocketPacket, ...]:
    """`0|TR_ID|record_count|field^...` 공개 시세 frame을 엄격히 분해한다."""

    parts = raw_frame.split("|", 3)
    if len(parts) != 4:
        raise KisH1WireError("KIS data frame은 pipe 4구간이어야 한다")
    encryption_flag, tr_id, record_count_text, wire_payload = parts
    if encryption_flag != "0":
        raise KisH1WireError("H1 공개 시세 수집기는 암호화·개인 데이터 frame을 허용하지 않는다")
    spec = h1_feed_spec(tr_id)
    try:
        record_count = int(record_count_text)
    except ValueError as exc:
        raise KisH1WireError("KIS record_count가 정수가 아니다") from exc
    if record_count <= 0:
        raise KisH1WireError("KIS record_count는 양수여야 한다")

    all_values = wire_payload.split("^")
    expected_count = record_count * len(spec.fields)
    if len(all_values) != expected_count:
        raise KisH1WireError(
            f"{tr_id} schema drift: values={len(all_values)}, expected={expected_count}"
        )

    packets = []
    width = len(spec.fields)
    for index in range(record_count):
        start = index * width
        values = tuple(all_values[start : start + width])
        sequence = provider_sequence
        if sequence is not None and record_count > 1:
            sequence = f"{sequence}:{index}"
        packets.append(
            KisH1WebSocketPacket(
                tr_id=tr_id,
                fields=spec.fields,
                values=values,
                raw_frame=raw_frame,
                record_frame=f"0|{tr_id}|1|{'^'.join(values)}",
                received_time_utc=received_time_utc,
                provider_sequence=sequence,
            )
        )
    return tuple(packets)
