"""Open API 미제공 일반수급·[MDCSTAT300] 수동 CSV 백필.

원본 바이트의 SHA-256을 주소로 삼아 raw·normalized·lineage 산출물을
최초 한 번만 생성한다. 동일 해시 재실행은 멱등이며, 기존 파일을
덮어쓰거나 값을 추정·합성하지 않는다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as wall_time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from skhy_research.features.g9_idiosyncratic_flow import (
    InvestorFlowScope,
    InvestorNetBuyObservation,
    ShortSaleObservation,
)

_SEOUL = ZoneInfo("Asia/Seoul")
_NS_PER_SECOND = 1_000_000_000
_DATE_HEADERS = ("trading_date", "일자", "날짜", "date", "거래일자")
_SYMBOL_HEADERS = ("종목코드", "단축코드", "isu_srt_cd", "symbol")


@dataclass(frozen=True)
class InvestorFlowCsvLoad:
    scope: InvestorFlowScope
    observations: tuple[InvestorNetBuyObservation, ...]
    file_sha256: str
    source_path: Path
    encoding: str
    date_column: str
    investor_columns: tuple[str, ...]
    trading_day_count: int


@dataclass(frozen=True)
class ShortSaleCsvLoad:
    symbol: str
    observations: tuple[ShortSaleObservation, ...]
    file_sha256: str
    source_path: Path
    encoding: str
    date_column: str
    volume_column: str | None
    balance_column: str | None
    trading_day_count: int


@dataclass(frozen=True)
class AppendOnlyBackfillArtifact:
    dataset: str
    content_sha256: str
    raw_path: str
    normalized_path: str
    lineage_path: str
    duplicate: bool
    record_count: int


def load_krx_investor_net_buy_csv(
    path: Path,
    *,
    scope: InvestorFlowScope,
) -> InvestorFlowCsvLoad:
    """투자자별 순매수대금 wide CSV를 원본 부호 그대로 적재한다.

    ``scope``는 파일명으로 추측하지 않고 호출자가 000660, 005930,
    반도체 관련 집계, 시장 중 하나를 명시해야 한다.
    """

    raw, text, encoding, headers, rows = _read_csv(path)
    date_column = _find_date_column(headers)
    symbol_column = _find_optional_header(headers, _SYMBOL_HEADERS)
    ignored = {date_column, symbol_column, "전체", "total"}
    investor_columns = tuple(header for header in headers if header not in ignored)
    if not investor_columns:
        raise ValueError("투자자별 CSV에 투자자 순매수 컬럼이 없다")

    file_hash = hashlib.sha256(raw).hexdigest()
    observations: list[InvestorNetBuyObservation] = []
    seen: set[tuple[date, str]] = set()
    trading_days: set[date] = set()
    for row in rows:
        trading_date = _parse_date(row.get(date_column))
        if trading_date is None:
            continue
        if symbol_column is not None and scope in (
            InvestorFlowScope.SKHY_000660,
            InvestorFlowScope.SAMSUNG_005930,
        ):
            requested_symbol = scope.value
            row_symbol = str(row.get(symbol_column, "")).strip().zfill(6)
            if row_symbol and row_symbol != requested_symbol:
                continue
        event_utc = _seoul_nanos(trading_date, wall_time(15, 30))
        available_utc = _seoul_nanos(trading_date, wall_time(18, 10))
        for investor in investor_columns:
            value = _decimal(row.get(investor))
            if value is None:
                continue
            key = (trading_date, investor)
            if key in seen:
                raise ValueError(
                    f"투자자별 CSV 중복: {trading_date.isoformat()}:{investor}"
                )
            seen.add(key)
            trading_days.add(trading_date)
            observations.append(
                InvestorNetBuyObservation(
                    trading_date=trading_date,
                    scope=scope,
                    investor=investor,
                    net_buy_notional=value,
                    event_time_utc=event_utc,
                    available_at_utc=available_utc,
                    source=f"KRX_MDS_MANUAL_CSV:INVESTOR_NET_BUY:{scope.value}",
                    input_record_id=(
                        f"krx-mds-manual:investor-net-buy:{scope.value}:"
                        f"{trading_date:%Y%m%d}:{_slug(investor)}:{file_hash[:16]}"
                    ),
                )
            )
    if not observations:
        raise ValueError(f"투자자별 CSV에 유효한 순매수 행이 없다: {path}")
    observations.sort(key=lambda item: (item.trading_date, item.investor))
    return InvestorFlowCsvLoad(
        scope=scope,
        observations=tuple(observations),
        file_sha256=file_hash,
        source_path=path,
        encoding=encoding,
        date_column=date_column,
        investor_columns=investor_columns,
        trading_day_count=len(trading_days),
    )


def load_krx_mdcstat300_short_sale_csv(
    path: Path,
    *,
    symbol: str = "000660",
) -> ShortSaleCsvLoad:
    """[MDCSTAT300] 개별종목 공매도 거래량·잔고 CSV를 적재한다."""

    raw, _, encoding, headers, rows = _read_csv(path)
    date_column = _find_date_column(headers)
    symbol_column = _find_optional_header(headers, _SYMBOL_HEADERS)
    volume_candidates = [header for header in headers if _is_short_volume(header)]
    balance_candidates = [header for header in headers if _is_short_balance(header)]
    if len(volume_candidates) > 1 or len(balance_candidates) > 1:
        raise ValueError(
            "[MDCSTAT300] 공매도 거래량/잔고 컬럼이 모호하다: "
            f"volume={volume_candidates}, balance={balance_candidates}"
        )
    volume_column = volume_candidates[0] if volume_candidates else None
    balance_column = balance_candidates[0] if balance_candidates else None
    if volume_column is None and balance_column is None:
        raise ValueError(
            "[MDCSTAT300] CSV에 공매도 거래량 또는 잔고 컬럼이 없다"
        )

    normalized_symbol = symbol.strip().zfill(6)
    file_hash = hashlib.sha256(raw).hexdigest()
    observations: list[ShortSaleObservation] = []
    seen: set[date] = set()
    for row in rows:
        trading_date = _parse_date(row.get(date_column))
        if trading_date is None:
            continue
        if symbol_column is not None:
            row_symbol = str(row.get(symbol_column, "")).strip().zfill(6)
            if row_symbol and row_symbol != normalized_symbol:
                continue
        volume = _decimal(row.get(volume_column)) if volume_column is not None else None
        balance = _decimal(row.get(balance_column)) if balance_column is not None else None
        if volume is None and balance is None:
            continue
        if trading_date in seen:
            raise ValueError(
                f"[MDCSTAT300] CSV에 중복 일자가 있다: {trading_date.isoformat()}"
            )
        seen.add(trading_date)
        observations.append(
            ShortSaleObservation(
                trading_date=trading_date,
                symbol=normalized_symbol,
                short_volume=volume,
                short_balance=balance,
                event_time_utc=_seoul_nanos(trading_date, wall_time(15, 30)),
                volume_available_at_utc=_seoul_nanos(trading_date, wall_time(18, 10)),
                source="KRX_MDS_MANUAL_CSV:[MDCSTAT300]",
                input_record_id=(
                    f"krx-mds-manual:MDCSTAT300:{normalized_symbol}:"
                    f"{trading_date:%Y%m%d}:{file_hash[:16]}"
                ),
            )
        )
    if not observations:
        raise ValueError(f"[MDCSTAT300] CSV에 {normalized_symbol} 유효 행이 없다: {path}")
    observations.sort(key=lambda item: item.trading_date)
    return ShortSaleCsvLoad(
        symbol=normalized_symbol,
        observations=tuple(observations),
        file_sha256=file_hash,
        source_path=path,
        encoding=encoding,
        date_column=date_column,
        volume_column=volume_column,
        balance_column=balance_column,
        trading_day_count=len(observations),
    )


def persist_investor_flow_append_only(
    load: InvestorFlowCsvLoad, data_root: Path
) -> AppendOnlyBackfillArtifact:
    dataset = f"krx_investor_net_buy/{load.scope.value}"
    normalized = {
        "dataset": dataset,
        "source": "KRX_DATA_MARKETPLACE_MANUAL_CSV",
        "open_api_status": "NOT_IN_OFFICIAL_KRX_OPEN_API_CATALOG",
        "scope": load.scope.value,
        "file_sha256": load.file_sha256,
        "encoding": load.encoding,
        "date_column": load.date_column,
        "investor_columns": load.investor_columns,
        "records": [
            {
                "trading_date": item.trading_date.isoformat(),
                "investor": item.investor,
                "net_buy_notional_krw": str(item.net_buy_notional),
                "event_time_utc": item.event_time_utc,
                "available_at_utc": item.available_at_utc,
                "input_record_id": item.input_record_id,
            }
            for item in load.observations
        ],
    }
    return _persist_append_only(
        data_root=data_root,
        dataset=dataset,
        source_path=load.source_path,
        content_hash=load.file_sha256,
        normalized=normalized,
        record_count=len(load.observations),
    )


def persist_short_sale_append_only(
    load: ShortSaleCsvLoad, data_root: Path
) -> AppendOnlyBackfillArtifact:
    dataset = f"krx_short_selling_comprehensive/{load.symbol}"
    normalized = {
        "dataset": dataset,
        "source": "KRX_DATA_MARKETPLACE_MANUAL_CSV:[MDCSTAT300]",
        "open_api_status": "NOT_IN_OFFICIAL_KRX_OPEN_API_CATALOG",
        "symbol": load.symbol,
        "file_sha256": load.file_sha256,
        "encoding": load.encoding,
        "date_column": load.date_column,
        "volume_column": load.volume_column,
        "balance_column": load.balance_column,
        "balance_publication_lag_trading_days": 2,
        "records": [
            {
                "trading_date": item.trading_date.isoformat(),
                "short_volume": str(item.short_volume) if item.short_volume is not None else None,
                "short_balance": str(item.short_balance) if item.short_balance is not None else None,
                "event_time_utc": item.event_time_utc,
                "volume_available_at_utc": item.volume_available_at_utc,
                "input_record_id": item.input_record_id,
            }
            for item in load.observations
        ],
    }
    return _persist_append_only(
        data_root=data_root,
        dataset=dataset,
        source_path=load.source_path,
        content_hash=load.file_sha256,
        normalized=normalized,
        record_count=len(load.observations),
    )


def _persist_append_only(
    *,
    data_root: Path,
    dataset: str,
    source_path: Path,
    content_hash: str,
    normalized: Mapping[str, Any],
    record_count: int,
) -> AppendOnlyBackfillArtifact:
    raw_path = data_root / "raw" / dataset / f"{content_hash}.csv"
    normalized_path = data_root / "normalized" / dataset / f"{content_hash}.json"
    lineage_path = data_root / "lineage" / dataset / f"{content_hash}.json"
    duplicate = raw_path.exists() and normalized_path.exists() and lineage_path.exists()
    raw_bytes = source_path.read_bytes()
    if hashlib.sha256(raw_bytes).hexdigest() != content_hash:
        raise ValueError("적재 중 원본 CSV checksum이 변경됐다")

    _write_once(raw_path, raw_bytes)
    normalized_bytes = json.dumps(
        normalized, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")
    _write_once(normalized_path, normalized_bytes)
    lineage = {
        "lineage_version": "krx-general-flow-manual-v1",
        "append_only": True,
        "raw_sha256": content_hash,
        "raw_path": str(raw_path),
        "normalized_path": str(normalized_path),
        "normalized_sha256": hashlib.sha256(normalized_bytes).hexdigest(),
        "record_count": record_count,
        "transform": "PARSE_ONLY_NO_SYNTHESIS",
    }
    _write_once(
        lineage_path,
        json.dumps(lineage, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )
    return AppendOnlyBackfillArtifact(
        dataset=dataset,
        content_sha256=content_hash,
        raw_path=str(raw_path),
        normalized_path=str(normalized_path),
        lineage_path=str(lineage_path),
        duplicate=duplicate,
        record_count=record_count,
    )


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise RuntimeError(f"APPEND_ONLY_CONTENT_CONFLICT:{path}") from None


def _read_csv(
    path: Path,
) -> tuple[bytes, str, str, tuple[str, ...], list[dict[str, str]]]:
    raw = path.read_bytes()
    text: str | None = None
    encoding = ""
    for candidate in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"KRX CSV 인코딩을 해독할 수 없다: {path}")
    reader = csv.DictReader(io.StringIO(text))
    headers = tuple(str(item).strip() for item in (reader.fieldnames or ()) if item)
    if not headers:
        raise ValueError(f"KRX CSV에 헤더가 없다: {path}")
    rows = [
        {str(key).strip(): str(value).strip() for key, value in row.items() if key is not None}
        for row in reader
    ]
    return raw, text, encoding, headers, rows


def _find_date_column(headers: tuple[str, ...]) -> str:
    match = _find_optional_header(headers, _DATE_HEADERS)
    if match is None:
        match = next((item for item in headers if "일자" in item or "날짜" in item), None)
    if match is None:
        raise ValueError(f"KRX CSV에 날짜 컬럼이 없다: {headers}")
    return match


def _find_optional_header(headers: tuple[str, ...], candidates: tuple[str, ...]) -> str | None:
    lowered = {candidate.lower() for candidate in candidates}
    return next((item for item in headers if item.lower() in lowered), None)


def _parse_date(raw: Any) -> date | None:
    text = str(raw if raw is not None else "").strip()
    digits = text.replace("-", "").replace("/", "").replace(".", "").replace(" ", "")
    if len(digits) != 8 or not digits.isdigit():
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def _decimal(raw: Any) -> Decimal | None:
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "null", "None"}:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _compact(header: str) -> str:
    return header.lower().replace(" ", "").replace("_", "").replace("(", "").replace(")", "")


def _is_short_volume(header: str) -> bool:
    compact = _compact(header)
    return "공매도" in compact and "잔고" not in compact and (
        "거래량" in compact or "수량" in compact
    )


def _is_short_balance(header: str) -> bool:
    compact = _compact(header)
    return "공매도" in compact and "잔고" in compact and (
        "수량" in compact or "금액" in compact
    )


def _slug(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _seoul_nanos(day: date, clock_time: wall_time) -> int:
    return int(datetime.combine(day, clock_time, tzinfo=_SEOUL).timestamp() * _NS_PER_SECOND)
