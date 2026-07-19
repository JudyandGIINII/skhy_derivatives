"""무료 KRX 일별 데이터 기반 H1 flow 사전반증 회귀 스터디.

이 모듈은 페이퍼 연구 전용이며 주문·broker 경로를 포함하지 않는다. strong
변형은 ``stk_bydd_trd``에 없는 프로그램매매·15:20 직전가·종가경매 구간
거래대금을 다른 값으로 대체하지 않는다. ``weak_daily_v1``은 시가→종가 수익을
별도 대체 Y로 라벨하고, 프로그램 X가 없으면 명시적 실행보류로 남긴다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as wall_time
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow.parquet as pq

from skhy_research.ports.errors import (
    ProviderAccessDeniedError,
    ProviderAuthenticationError,
)

PREFALSIFICATION_STUDY_VERSION = "h1_prefalsification_lag1_daily_v1"
WEAK_DAILY_STUDY_VERSION = "h1_prefalsification_weak_daily_v1"
PREFALSIFICATION_SCOPE = "live-collection-go-no-go-only"
PREFALSIFICATION_DATA_RESOLUTION = "krx-historical-daily-prefalsification"
CONTROL_FACTOR_NAMES = (
    "kospi_return",
    "krx_semiconductor_return",
    "samsung_005930_return",
)
ADV_LOOKBACK_DAYS = 20
PROGRAM_LAG_TRADING_DAYS = 1
MINIMUM_RAW_TRADING_DAYS = 756
MINIMUM_USABLE_OBSERVATIONS = 735
MINIMUM_REGRESSION_OBSERVATIONS = 30
HAC_T_THRESHOLD = Decimal("1.96")
SIGNIFICANCE_LEVEL = Decimal("0.05")
DEFAULT_PERMUTATIONS = 2000
DEFAULT_BOOTSTRAP_RESAMPLES = 2000
BLOCK_BOOTSTRAP_DAYS = 20
PRE_AUCTION_MAX_AGE_SECONDS = 60
_NANOSECONDS_PER_SECOND = 1_000_000_000
_SEOUL = ZoneInfo("Asia/Seoul")


class PrefalsificationModelVariant(StrEnum):
    STRONG_CLOSE_AUCTION_V1 = "strong_close_auction_v1"
    WEAK_DAILY_V1 = "weak_daily_v1"


class PrefalsificationDataOrigin(StrEnum):
    KRX_HISTORICAL_ACTUAL = "KRX_HISTORICAL_ACTUAL"
    SANITIZED_FIXTURE = "SANITIZED_FIXTURE"


class PrefalsificationStatus(StrEnum):
    COMPLETED = "COMPLETED"
    HOLD_DATA_UNAVAILABLE = "HOLD_DATA_UNAVAILABLE"
    HOLD_SAMPLE_INSUFFICIENT = "HOLD_SAMPLE_INSUFFICIENT"
    FIXTURE_ONLY = "FIXTURE_ONLY"


class PrefalsificationVerdict(StrEnum):
    FALSIFY = "FALSIFY"
    PROCEED_TO_LIVE = "PROCEED_TO_LIVE"
    HOLD = "HOLD"


class RegressionVariant(StrEnum):
    RAW = "RAW"
    COMMON_FACTOR_RESIDUAL = "COMMON_FACTOR_RESIDUAL"


@dataclass(frozen=True)
class FieldSpecification:
    name: str
    role: str
    source: str
    raw_unit: str
    transformation: str
    timing: str
    lookahead_rule: str


PREFALSIFICATION_FIELD_SPECIFICATIONS = (
    FieldSpecification(
        name="x_program_lag1_adv20",
        role="X",
        source="KRX 정보데이터시스템 [12009] 종목별 프로그램매매 합계 순매수대금",
        raw_unit="KRW",
        transformation="program_net_buy_notional(t-1) / mean(total_turnover(t-20..t-1))",
        timing="t-1 종가 후 공표되어 t의 15:20 전에 가용",
        lookahead_rule="당일 종일 누적 프로그램 값은 금지하고 1거래일 시차·경제적 부호 +1 고정",
    ),
    FieldSpecification(
        name="y_signed_close_auction_notional_adv20",
        role="Y",
        source="KRX 15:20 직전 연속장 최종가·공식 종가·종가 단일가 구간 거래대금",
        raw_unit="KRW price, KRW notional",
        transformation=(
            "sign(log(official_close/pre_auction_reference)) * "
            "close_auction_turnover_notional / ADV20"
        ),
        timing="15:20 직전 기준가는 경매 시작 전, 종가·경매대금은 15:30 outcome",
        lookahead_rule="전일종가·시가·일별 총거래대금으로 대체 금지; Y는 회귀 outcome에만 사용",
    ),
    FieldSpecification(
        name="common_factor_returns",
        role="CONTROL_DIAGNOSTIC",
        source="KRX KOSPI·KRX 반도체지수·삼성전자 005930 일별 수익률",
        raw_unit="decimal return",
        transformation="X와 Y를 동일 고정 control matrix에 각각 투영한 FWL 잔차",
        timing="당일 공식 종가 후 확정되는 사후 nuisance control",
        lookahead_rule="신호 입력·거래·raw 회귀에는 사용하지 않고 사후 공통요인 제거에만 사용",
    ),
)

WEAK_DAILY_FIELD_SPECIFICATIONS = (
    FieldSpecification(
        name="x_program_lag1_adv20",
        role="X",
        source="KRX 정보데이터시스템 [12009] 종목별 프로그램매매 합계 순매수대금",
        raw_unit="KRW",
        transformation="program_net_buy_notional(t-1) / mean(total_turnover(t-20..t-1))",
        timing="t-1 종가 후 공표되어 t 시가 전에 가용",
        lookahead_rule="당일 종일 프로그램 합계를 당일 Y의 X로 쓰지 않고 1거래일 시차를 고정",
    ),
    FieldSpecification(
        name="y_weak_open_to_close_return",
        role="Y_WEAK_PROXY",
        source="KRX Open API stk_bydd_trd 000660 TDD_OPNPRC·TDD_CLSPRC",
        raw_unit="KRW price -> decimal return",
        transformation="log(TDD_CLSPRC(t) / TDD_OPNPRC(t))",
        timing="09:00 시가와 15:30 공식 종가로 구성되며 Y outcome은 종가 후 확정",
        lookahead_rule=(
            "Y는 회귀 outcome에만 사용; 15:20 직전가나 종가경매 수익으로 "
            "오인하지 않음"
        ),
    ),
    FieldSpecification(
        name="weak_common_factor_returns",
        role="CONTROL_DIAGNOSTIC",
        source="KRX KOSPI·KRX 반도체지수·삼성전자 005930 일별 시가→종가 수익률",
        raw_unit="decimal return",
        transformation="X와 weak Y를 동일 고정 control matrix에 각각 투영한 FWL 잔차",
        timing="당일 공식 종가 후 확정되는 사후 nuisance control",
        lookahead_rule="신호 입력이 아닌 사후 공통요인 제거 진단에만 사용",
    ),
)


@dataclass(frozen=True)
class TimedStudyValue:
    value: Decimal | None
    event_time_utc: int | None
    available_at_utc: int | None
    source: str
    unit: str
    input_record_id: str | None
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            if not self.missing_reason:
                raise ValueError("결측 study 값에는 missing_reason이 필요하다")
            return
        if not self.value.is_finite():
            raise ValueError("study 값은 유한해야 한다")
        if self.event_time_utc is None or self.event_time_utc < 0:
            raise ValueError("관측값에는 event_time_utc가 필요하다")
        if self.available_at_utc is None or self.available_at_utc < self.event_time_utc:
            raise ValueError("available_at_utc는 event_time_utc보다 이를 수 없다")
        if not self.source.strip() or not self.unit.strip():
            raise ValueError("관측값에는 source와 unit이 필요하다")
        if self.input_record_id is None or not self.input_record_id.strip():
            raise ValueError("관측값에는 input_record_id가 필요하다")
        if self.missing_reason is not None:
            raise ValueError("관측값에는 missing_reason을 둘 수 없다")


@dataclass(frozen=True)
class PrefalsificationDailyObservation:
    trading_date: date
    symbol: str
    auction_start_utc: int
    auction_end_utc: int
    program_net_buy_notional: TimedStudyValue
    pre_auction_reference_price: TimedStudyValue
    official_close_price: TimedStudyValue
    close_auction_turnover_notional: TimedStudyValue
    total_turnover_notional: TimedStudyValue
    control_returns: Mapping[str, TimedStudyValue]
    data_origin: PrefalsificationDataOrigin

    def __post_init__(self) -> None:
        if self.symbol != "000660":
            raise ValueError("사전반증 주대상은 KRX 000660만 허용한다")
        if self.auction_start_utc >= self.auction_end_utc:
            raise ValueError("auction_end_utc는 auction_start_utc보다 늦어야 한다")


@dataclass(frozen=True)
class WeakDailyObservation:
    """15:20 직전가가 없는 무료 일별 weak proxy 입력."""

    trading_date: date
    symbol: str
    market_open_utc: int
    official_close_utc: int
    program_net_buy_notional: TimedStudyValue
    official_open_price: TimedStudyValue
    official_close_price: TimedStudyValue
    total_turnover_notional: TimedStudyValue
    control_returns: Mapping[str, TimedStudyValue]
    data_origin: PrefalsificationDataOrigin

    def __post_init__(self) -> None:
        if self.symbol != "000660":
            raise ValueError("사전반증 주대상은 KRX 000660만 허용한다")
        if self.market_open_utc >= self.official_close_utc:
            raise ValueError("official_close_utc는 market_open_utc보다 늦어야 한다")


@dataclass(frozen=True)
class PrefalsificationRegressionRow:
    trading_date: date
    x_program_lag1_adv20: Decimal
    y_signed_close_auction_notional_adv20: Decimal
    auction_residual_return: Decimal
    adv20_notional: Decimal
    control_returns: Mapping[str, Decimal] | None
    input_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class RegressionBuildResult:
    rows: tuple[PrefalsificationRegressionRow, ...]
    scheduled_observations: int
    raw_eligible_count: int
    controlled_eligible_count: int
    missing_reason_counts: Mapping[str, int]


@dataclass(frozen=True)
class RegressionStatistics:
    variant: RegressionVariant
    observation_count: int
    hac_max_lags: int
    intercept: Decimal
    beta: Decimal
    hac_standard_error: Decimal
    t_statistic: Decimal
    analytic_two_sided_p: Decimal
    permutation_p_value: Decimal
    block_bootstrap_ci: tuple[Decimal, Decimal]
    standardized_effect_size: Decimal
    r_squared: Decimal
    control_names: tuple[str, ...]


@dataclass(frozen=True)
class ModelAssessment:
    statistics: RegressionStatistics
    verdict: PrefalsificationVerdict
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DataAvailabilityAudit:
    ohlcv_bar_count: int
    ohlcv_symbols: tuple[str, ...]
    available_datasets: tuple[str, ...]
    missing_required_datasets: tuple[str, ...]
    inspected_paths: tuple[str, ...]
    data_snapshot_hash: str
    dataset_coverage: Mapping[str, int] = field(default_factory=dict)
    endpoint_status: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WeakDailyCollectionResult:
    observations: tuple[WeakDailyObservation, ...]
    availability_audit: DataAvailabilityAudit
    snapshot_path: str


@dataclass(frozen=True)
class PrefalsificationStudyConfig:
    seed: int = 7
    permutations: int = DEFAULT_PERMUTATIONS
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES
    bootstrap_block_days: int = BLOCK_BOOTSTRAP_DAYS

    def __post_init__(self) -> None:
        if self.permutations <= 0 or self.bootstrap_resamples <= 0:
            raise ValueError("permutations와 bootstrap_resamples는 양수여야 한다")
        if self.bootstrap_block_days <= 0:
            raise ValueError("bootstrap_block_days는 양수여야 한다")


@dataclass(frozen=True)
class PrefalsificationStudyResult:
    status: PrefalsificationStatus
    verdict: PrefalsificationVerdict
    reasons: tuple[str, ...]
    data_origin: PrefalsificationDataOrigin
    scheduled_observations: int
    raw_eligible_count: int
    controlled_eligible_count: int
    missing_reason_counts: Mapping[str, int]
    raw_model: ModelAssessment | None
    controlled_model: ModelAssessment | None
    data_snapshot_hash: str
    input_record_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    model_variant: PrefalsificationModelVariant = (
        PrefalsificationModelVariant.STRONG_CLOSE_AUCTION_V1
    )
    study_version: str = PREFALSIFICATION_STUDY_VERSION
    data_resolution: str = PREFALSIFICATION_DATA_RESOLUTION
    promotion_scope: str = PREFALSIFICATION_SCOPE
    paper_only: bool = True
    order_submission_enabled: bool = False
    availability_audit: DataAvailabilityAudit | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "study_version": self.study_version,
            "model_variant": self.model_variant.value,
            "status": self.status.value,
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "data_origin": self.data_origin.value,
            "data_resolution": self.data_resolution,
            "promotion_scope": self.promotion_scope,
            "paper_only": self.paper_only,
            "order_submission_enabled": self.order_submission_enabled,
            "scheduled_observations": self.scheduled_observations,
            "raw_eligible_count": self.raw_eligible_count,
            "controlled_eligible_count": self.controlled_eligible_count,
            "missing_reason_counts": dict(self.missing_reason_counts),
            "data_snapshot_hash": self.data_snapshot_hash,
            "input_record_ids": list(self.input_record_ids),
            "field_specifications": [
                {
                    "name": item.name,
                    "role": item.role,
                    "source": item.source,
                    "raw_unit": item.raw_unit,
                    "transformation": item.transformation,
                    "timing": item.timing,
                    "lookahead_rule": item.lookahead_rule,
                }
                for item in _field_specifications(self.model_variant)
            ],
            "raw_model": _assessment_dict(self.raw_model),
            "controlled_model": _assessment_dict(self.controlled_model),
            "warnings": list(self.warnings),
            "availability_audit": _availability_audit_dict(self.availability_audit),
        }


class _KrxWeakDailyClient(Protocol):
    def fetch_daily_stock_trades(self, trading_date: date) -> list[dict[str, Any]]: ...

    def fetch_daily_krx_index_trades(
        self, trading_date: date
    ) -> list[dict[str, Any]]: ...

    def fetch_daily_kospi_index_trades(
        self, trading_date: date
    ) -> list[dict[str, Any]]: ...


def collect_krx_weak_daily_inputs(
    client: _KrxWeakDailyClient,
    *,
    end: date,
    output_path: Path,
    minimum_trading_days: int = MINIMUM_RAW_TRADING_DAYS,
    max_lookback_calendar_days: int = 1200,
    min_request_interval_seconds: float = 0.2,
    program_csv_path: Path | None = None,
) -> WeakDailyCollectionResult:
    """KRX 조회 전용 API로 weak proxy의 확보 가능 입력만 수집한다.

    Open API 목록에 없는 [12009] program 값은 기본적으로 모든 거래일에
    ``value=None``으로 보존한다. ``program_csv_path``가 주어지면 사용자가
    `data.krx.co.kr`에서 수동 내려받은 [12009] CSV의 합계 순매수대금으로만
    채우며, 이 함수는 어떤 추정값도 생성하지 않는다.
    """

    if minimum_trading_days <= 0:
        raise ValueError("minimum_trading_days는 양수여야 한다")
    if max_lookback_calendar_days < minimum_trading_days:
        raise ValueError("max_lookback_calendar_days가 거래일 목표보다 짧다")
    if min_request_interval_seconds < 0:
        raise ValueError("min_request_interval_seconds는 음수일 수 없다")

    program_load: ProgramTradeCsvLoad | None = None
    if program_csv_path is not None:
        program_load = load_krx_12009_program_net_buy(program_csv_path)
    program_by_date: Mapping[date, Decimal] = (
        program_load.by_date if program_load is not None else {}
    )
    program_file_hash = program_load.file_sha256 if program_load is not None else None
    program_missing_reason = (
        "PROGRAM_12009_CSV_DATE_MISSING"
        if program_load is not None
        else "PROGRAM_12009_NOT_IN_KRX_OPEN_API_CATALOG"
    )

    last_request_at: float | None = None

    def paced(fetch: Any, trading_date: date) -> list[dict[str, Any]]:
        nonlocal last_request_at
        if last_request_at is not None:
            remaining = min_request_interval_seconds - (time.monotonic() - last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        try:
            return cast(list[dict[str, Any]], fetch(trading_date))
        finally:
            last_request_at = time.monotonic()

    endpoint_status: dict[str, str] = {
        "krx_program_trading_daily_12009": (
            f"MANUAL_CSV_LOADED:{program_load.file_sha256[:16]}"
            if program_load is not None
            else "NOT_IN_OFFICIAL_KRX_OPEN_API_CATALOG"
        ),
        "krx_stock_daily": "PENDING",
        "krx_kospi_index_daily": "PENDING",
        "krx_semiconductor_index_daily": "PENDING",
    }
    semiconductor_name: str | None = None
    probe = end
    for _ in range(10):
        if probe.weekday() >= 5:
            probe -= timedelta(days=1)
            continue
        try:
            rows = paced(client.fetch_daily_krx_index_trades, probe)
        except (ProviderAuthenticationError, ProviderAccessDeniedError):
            endpoint_status["krx_semiconductor_index_daily"] = (
                "AUTHENTICATED_KEY_NOT_ENTITLED_TO_KRX_INDEX_ENDPOINT"
            )
            break
        names = sorted(
            str(row.get("IDX_NM", "")).strip()
            for row in rows
            if "반도체" in str(row.get("IDX_NM", ""))
        )
        if names:
            semiconductor_name = (
                "KRX 반도체" if "KRX 반도체" in names else names[0]
            )
            endpoint_status["krx_semiconductor_index_daily"] = (
                f"AVAILABLE:{semiconductor_name}"
            )
            break
        if rows:
            endpoint_status["krx_semiconductor_index_daily"] = (
                "NO_SEMICONDUCTOR_SERIES_IN_RESPONSE"
            )
            break
        probe -= timedelta(days=1)

    observations_descending: list[WeakDailyObservation] = []
    snapshot_records_descending: list[dict[str, object]] = []
    lower_bound = end - timedelta(days=max_lookback_calendar_days - 1)
    current = end
    while current >= lower_bound and len(observations_descending) < minimum_trading_days:
        if current.weekday() >= 5:
            current -= timedelta(days=1)
            continue
        stock_rows = paced(client.fetch_daily_stock_trades, current)
        skhy_row = _row_by_name(stock_rows, "ISU_CD", "000660")
        samsung_row = _row_by_name(stock_rows, "ISU_CD", "005930")
        if skhy_row is None or samsung_row is None:
            current -= timedelta(days=1)
            continue
        kospi_rows = paced(client.fetch_daily_kospi_index_trades, current)
        kospi_row = _row_by_name(kospi_rows, "IDX_NM", "코스피")
        if kospi_row is None:
            current -= timedelta(days=1)
            continue
        semiconductor_row: dict[str, Any] | None = None
        if semiconductor_name is not None:
            semiconductor_rows = paced(client.fetch_daily_krx_index_trades, current)
            semiconductor_row = _row_by_name(
                semiconductor_rows, "IDX_NM", semiconductor_name
            )
            if semiconductor_row is None:
                current -= timedelta(days=1)
                continue
        observation, snapshot_record = _weak_observation_from_krx_rows(
            current,
            skhy_row=skhy_row,
            samsung_row=samsung_row,
            kospi_row=kospi_row,
            semiconductor_row=semiconductor_row,
            semiconductor_name=semiconductor_name,
            program_value=program_by_date.get(current),
            program_file_hash=program_file_hash,
            program_missing_reason=program_missing_reason,
        )
        observations_descending.append(observation)
        snapshot_records_descending.append(snapshot_record)
        current -= timedelta(days=1)

    observations = tuple(reversed(observations_descending))
    snapshot_records = list(reversed(snapshot_records_descending))
    if observations:
        endpoint_status["krx_stock_daily"] = "AVAILABLE"
        endpoint_status["krx_kospi_index_daily"] = "AVAILABLE"
    else:
        endpoint_status["krx_stock_daily"] = "NO_ALIGNED_TARGET_ROWS"
        endpoint_status["krx_kospi_index_daily"] = "NO_ALIGNED_TARGET_ROWS"
    program_covered = sum(
        1 for obs in observations if obs.program_net_buy_notional.value is not None
    )
    snapshot_payload = {
        "model_variant": PrefalsificationModelVariant.WEAK_DAILY_V1.value,
        "source": "KRX_OPEN_API_READ_ONLY",
        "paper_only": True,
        "order_submission_enabled": False,
        "program_api_catalog_status": (
            "MANUAL_CSV_LOADED"
            if program_load is not None
            else "NOT_IN_OFFICIAL_KRX_OPEN_API_CATALOG"
        ),
        "program_manual_csv": (
            {
                "file_sha256": program_load.file_sha256,
                "date_column": program_load.date_column,
                "value_column": program_load.value_column,
                "encoding": program_load.encoding,
                "csv_row_count": program_load.row_count,
                "matched_trading_days": program_covered,
            }
            if program_load is not None
            else None
        ),
        "endpoint_status": endpoint_status,
        "observations": snapshot_records,
    }
    encoded = json.dumps(
        snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    snapshot_hash = hashlib.sha256(encoded).hexdigest()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(snapshot_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    available = []
    if observations:
        available.extend(("krx_daily_ohlcv", "krx_kospi_index_daily"))
    if semiconductor_name is not None:
        available.append("krx_semiconductor_index_daily")
    if program_covered:
        available.append("krx_program_trading_daily_12009")
    required = (
        "krx_daily_ohlcv",
        "krx_program_trading_daily_12009",
        "krx_kospi_index_daily",
        "krx_semiconductor_index_daily",
    )
    audit = DataAvailabilityAudit(
        ohlcv_bar_count=len(observations) * 2,
        ohlcv_symbols=("000660", "005930") if observations else (),
        available_datasets=tuple(sorted(available)),
        missing_required_datasets=tuple(name for name in required if name not in available),
        inspected_paths=(str(output_path),),
        data_snapshot_hash=snapshot_hash,
        dataset_coverage={
            "krx_daily_ohlcv": len(observations),
            "krx_kospi_index_daily": len(observations),
            "krx_semiconductor_index_daily": (
                len(observations) if semiconductor_name is not None else 0
            ),
            "krx_program_trading_daily_12009": program_covered,
        },
        endpoint_status=endpoint_status,
    )
    return WeakDailyCollectionResult(
        observations=observations,
        availability_audit=audit,
        snapshot_path=str(output_path),
    )


@dataclass(frozen=True)
class ProgramTradeCsvLoad:
    """KRX 정보데이터시스템 [12009] 수동 CSV 적재 결과.

    Open API가 제공하지 않는 종목별 일별 프로그램매매 합계 순매수대금을,
    사용자가 `data.krx.co.kr`에서 수동 내려받은 CSV로만 채운다. 값은 어떤
    추정도 하지 않고 원본 그대로 파싱하며, 부호(순매수 +/-)를 보존한다.
    """

    by_date: Mapping[date, Decimal]
    file_sha256: str
    date_column: str
    value_column: str
    encoding: str
    row_count: int


_PROGRAM_CSV_DATE_HEADERS = ("trading_date", "일자", "날짜", "date", "거래일자")
_PROGRAM_CSV_SYMBOL_HEADERS = (
    "종목코드",
    "단축코드",
    "종목_코드",
    "isu_srt_cd",
    "symbol",
)
_PROGRAM_CSV_CANONICAL_VALUE = "program_net_buy_notional"


def _looks_like_net_buy_amount(header: str) -> bool:
    compact = header.replace(" ", "")
    return "순매수" in compact and ("거래대금" in compact or "대금" in compact)


def _parse_program_csv_date(raw: Any) -> date | None:
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    digits = (
        text.replace("-", "").replace("/", "").replace(".", "").replace(" ", "")
    )
    if len(digits) == 8 and digits.isdigit():
        try:
            return datetime.strptime(digits, "%Y%m%d").date()
        except ValueError:
            return None
    return None


def load_krx_12009_program_net_buy(
    path: Path, *, symbol: str = "000660"
) -> ProgramTradeCsvLoad:
    """KRX [12009] 종목별 프로그램매매 수동 CSV에서 합계 순매수대금을 적재한다.

    - 인코딩은 KRX 내보내기 관행(CP949/EUC-KR)과 UTF-8을 모두 시도한다.
    - 날짜 컬럼과 '전체(합계) 순매수 거래대금' 컬럼을 헤더로 자동 식별한다.
      식별 실패·모호(복수 후보)·중복 일자는 추정하지 않고 fail-closed로 오류를 낸다.
    - `program_net_buy_notional` 헤더의 정규화 CSV도 그대로 허용한다.
    """

    raw = path.read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()
    text: str | None = None
    used_encoding = ""
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"[12009] CSV 인코딩을 해독할 수 없다: {path}")

    reader = csv.DictReader(io.StringIO(text))
    headers = [str(name).strip() for name in (reader.fieldnames or []) if name]
    if not headers:
        raise ValueError(f"[12009] CSV에 헤더가 없다: {path}")

    date_column = next(
        (h for h in headers if h.lower() in _PROGRAM_CSV_DATE_HEADERS), None
    )
    if date_column is None:
        date_column = next(
            (h for h in headers if "일자" in h or "날짜" in h), None
        )
    if date_column is None:
        raise ValueError(
            f"[12009] CSV 날짜 컬럼을 찾지 못했다. 헤더: {headers}"
        )

    canonical = next(
        (h for h in headers if h.lower() == _PROGRAM_CSV_CANONICAL_VALUE), None
    )
    if canonical is not None:
        value_column = canonical
    else:
        candidates = [h for h in headers if _looks_like_net_buy_amount(h)]
        preferred = [h for h in candidates if "전체" in h or "합계" in h]
        pool = preferred or candidates
        if not pool:
            raise ValueError(
                "[12009] CSV에서 순매수 거래대금 컬럼을 찾지 못했다. "
                "'전체(합계) 순매수 거래대금' 컬럼이 필요하다. "
                f"헤더: {headers}"
            )
        if len(pool) > 1:
            raise ValueError(
                "[12009] CSV 순매수 거래대금 컬럼이 모호하다(복수 후보). "
                f"후보: {pool}. 전체/합계 순매수 거래대금 단일 컬럼만 남겨 다시 시도."
            )
        value_column = pool[0]

    symbol_column = next(
        (h for h in headers if h.lower() in _PROGRAM_CSV_SYMBOL_HEADERS), None
    )
    normalized_symbol = symbol.strip().zfill(6)

    by_date: dict[date, Decimal] = {}
    for row in reader:
        if symbol_column is not None:
            code = str(row.get(symbol_column, "")).strip().zfill(6)
            if code and code != normalized_symbol:
                continue
        trading_date = _parse_program_csv_date(row.get(date_column))
        if trading_date is None:
            continue
        value = _krx_decimal(row.get(value_column))
        if value is None:
            continue
        if trading_date in by_date:
            raise ValueError(
                f"[12009] CSV에 중복 일자가 있다: {trading_date.isoformat()}"
            )
        by_date[trading_date] = value

    if not by_date:
        raise ValueError(
            f"[12009] CSV에서 {symbol} 유효 행을 찾지 못했다: {path}"
        )

    return ProgramTradeCsvLoad(
        by_date=dict(sorted(by_date.items())),
        file_sha256=file_hash,
        date_column=date_column,
        value_column=value_column,
        encoding=used_encoding,
        row_count=len(by_date),
    )


def _row_by_name(
    rows: Sequence[dict[str, Any]], field_name: str, expected: str
) -> dict[str, Any] | None:
    return next(
        (row for row in rows if str(row.get(field_name, "")).strip() == expected),
        None,
    )


def _weak_observation_from_krx_rows(
    trading_date: date,
    *,
    skhy_row: Mapping[str, Any],
    samsung_row: Mapping[str, Any],
    kospi_row: Mapping[str, Any],
    semiconductor_row: Mapping[str, Any] | None,
    semiconductor_name: str | None,
    program_value: Decimal | None = None,
    program_file_hash: str | None = None,
    program_missing_reason: str = "PROGRAM_12009_NOT_IN_KRX_OPEN_API_CATALOG",
) -> tuple[WeakDailyObservation, dict[str, object]]:
    open_utc = _seoul_nanos(trading_date, wall_time(9, 0))
    close_utc = _seoul_nanos(trading_date, wall_time(15, 30))
    stock_record_id = _record_id("stk_bydd_trd", trading_date, (skhy_row, samsung_row))
    kospi_record_id = _record_id("kospi_dd_trd", trading_date, (kospi_row,))
    semiconductor_record_id = (
        _record_id("krx_dd_trd", trading_date, (semiconductor_row,))
        if semiconductor_row is not None
        else None
    )
    skhy_open = _krx_decimal(skhy_row.get("TDD_OPNPRC"))
    skhy_close = _krx_decimal(skhy_row.get("TDD_CLSPRC"))
    turnover = _krx_decimal(skhy_row.get("ACC_TRDVAL"))
    samsung_return = _open_to_close_return(
        samsung_row.get("TDD_OPNPRC"), samsung_row.get("TDD_CLSPRC")
    )
    kospi_return = _open_to_close_return(
        kospi_row.get("OPNPRC_IDX"), kospi_row.get("CLSPRC_IDX")
    )
    semiconductor_return = (
        _open_to_close_return(
            semiconductor_row.get("OPNPRC_IDX"), semiconductor_row.get("CLSPRC_IDX")
        )
        if semiconductor_row is not None
        else None
    )
    program_source = (
        "KRX_MDS_MANUAL_CSV:[12009]:program_net_buy_notional_total"
        if program_value is not None
        else "KRX_OPEN_API_CATALOG_AUDIT"
    )
    program_record_id = (
        f"krx-mds-manual-csv:12009:{trading_date:%Y%m%d}:{program_file_hash[:16]}"
        if program_value is not None and program_file_hash is not None
        else None
    )
    program_value_obj = _available_value(
        program_value,
        event_time_utc=close_utc,
        available_at_utc=close_utc,
        source=program_source,
        unit="KRW",
        record_id=program_record_id,
        missing_reason=program_missing_reason,
    )
    observation = WeakDailyObservation(
        trading_date=trading_date,
        symbol="000660",
        market_open_utc=open_utc,
        official_close_utc=close_utc,
        program_net_buy_notional=program_value_obj,
        official_open_price=_available_value(
            skhy_open,
            event_time_utc=open_utc,
            available_at_utc=close_utc,
            source="KRX_OPEN_API:stk_bydd_trd:TDD_OPNPRC",
            unit="KRW",
            record_id=stock_record_id,
            missing_reason="WEAK_OPEN_PRICE_MISSING",
        ),
        official_close_price=_available_value(
            skhy_close,
            event_time_utc=close_utc,
            available_at_utc=close_utc,
            source="KRX_OPEN_API:stk_bydd_trd:TDD_CLSPRC",
            unit="KRW",
            record_id=stock_record_id,
            missing_reason="OFFICIAL_CLOSE_MISSING",
        ),
        total_turnover_notional=_available_value(
            turnover,
            event_time_utc=close_utc,
            available_at_utc=close_utc,
            source="KRX_OPEN_API:stk_bydd_trd:ACC_TRDVAL",
            unit="KRW",
            record_id=stock_record_id,
            missing_reason="TOTAL_TURNOVER_MISSING",
        ),
        control_returns={
            "kospi_return": _available_value(
                kospi_return,
                event_time_utc=close_utc,
                available_at_utc=close_utc,
                source="KRX_OPEN_API:kospi_dd_trd:코스피:OPEN_TO_CLOSE",
                unit="RETURN",
                record_id=kospi_record_id,
                missing_reason="KOSPI_RETURN_MISSING",
            ),
            "krx_semiconductor_return": _available_value(
                semiconductor_return,
                event_time_utc=close_utc,
                available_at_utc=close_utc,
                source=(
                    f"KRX_OPEN_API:krx_dd_trd:{semiconductor_name}:OPEN_TO_CLOSE"
                    if semiconductor_name is not None
                    else "KRX_OPEN_API_ENDPOINT_ENTITLEMENT_AUDIT"
                ),
                unit="RETURN",
                record_id=semiconductor_record_id,
                missing_reason="KRX_SEMICONDUCTOR_INDEX_UNAVAILABLE",
            ),
            "samsung_005930_return": _available_value(
                samsung_return,
                event_time_utc=close_utc,
                available_at_utc=close_utc,
                source="KRX_OPEN_API:stk_bydd_trd:005930:OPEN_TO_CLOSE",
                unit="RETURN",
                record_id=stock_record_id,
                missing_reason="SAMSUNG_RETURN_MISSING",
            ),
        },
        data_origin=PrefalsificationDataOrigin.KRX_HISTORICAL_ACTUAL,
    )
    snapshot_record: dict[str, object] = {
        "trading_date": trading_date.isoformat(),
        "stock_record_id": stock_record_id,
        "kospi_record_id": kospi_record_id,
        "semiconductor_record_id": semiconductor_record_id,
        "000660": {
            "open": str(skhy_open) if skhy_open is not None else None,
            "close": str(skhy_close) if skhy_close is not None else None,
            "turnover": str(turnover) if turnover is not None else None,
        },
        "005930_open_to_close_return": (
            str(samsung_return) if samsung_return is not None else None
        ),
        "kospi_open_to_close_return": (
            str(kospi_return) if kospi_return is not None else None
        ),
        "krx_semiconductor_open_to_close_return": (
            str(semiconductor_return) if semiconductor_return is not None else None
        ),
        "program_net_buy_notional": (
            str(program_value) if program_value is not None else None
        ),
        "program_missing_reason": (
            None if program_value is not None else program_missing_reason
        ),
        "program_source": program_source,
        "program_file_sha256": (
            program_file_hash if program_value is not None else None
        ),
    }
    return observation, snapshot_record


def _available_value(
    value: Decimal | None,
    *,
    event_time_utc: int,
    available_at_utc: int,
    source: str,
    unit: str,
    record_id: str | None,
    missing_reason: str,
) -> TimedStudyValue:
    if value is None:
        return TimedStudyValue(
            value=None,
            event_time_utc=None,
            available_at_utc=None,
            source=source,
            unit=unit,
            input_record_id=None,
            missing_reason=missing_reason,
        )
    return TimedStudyValue(
        value=value,
        event_time_utc=event_time_utc,
        available_at_utc=available_at_utc,
        source=source,
        unit=unit,
        input_record_id=record_id,
    )


def _krx_decimal(value: Any) -> Decimal | None:
    normalized = str(value if value is not None else "").replace(",", "").strip()
    if not normalized or normalized == "-":
        return None
    parsed = Decimal(normalized)
    return parsed if parsed.is_finite() else None


def _open_to_close_return(open_value: Any, close_value: Any) -> Decimal | None:
    opening = _krx_decimal(open_value)
    closing = _krx_decimal(close_value)
    if opening is None or closing is None or opening <= 0 or closing <= 0:
        return None
    return Decimal(str(math.log(float(closing / opening))))


def _record_id(
    dataset: str, trading_date: date, rows: Sequence[Mapping[str, Any] | None]
) -> str:
    encoded = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"krx-open-api:{dataset}:{trading_date:%Y%m%d}:{digest}"


def _seoul_nanos(trading_date: date, clock_time: wall_time) -> int:
    return int(datetime.combine(trading_date, clock_time, _SEOUL).timestamp()) * (
        _NANOSECONDS_PER_SECOND
    )


def build_prefalsification_rows(
    observations: Sequence[PrefalsificationDailyObservation],
) -> RegressionBuildResult:
    """20일 과거 ADV와 lag-1 program만 사용해 시간순 회귀 행을 만든다."""

    ordered = tuple(observations)
    dates = [item.trading_date for item in ordered]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("observation은 중복 없는 거래일 오름차순이어야 한다")
    if len({item.data_origin for item in ordered}) > 1:
        raise ValueError("actual과 fixture data origin을 한 study에서 혼합할 수 없다")
    reasons: Counter[str] = Counter()
    rows: list[PrefalsificationRegressionRow] = []
    for index in range(ADV_LOOKBACK_DAYS, len(ordered)):
        current = ordered[index]
        previous = ordered[index - PROGRAM_LAG_TRADING_DAYS]
        adv_window = ordered[index - ADV_LOOKBACK_DAYS : index]
        row = _build_one_row(current, previous, adv_window, reasons)
        if row is not None:
            rows.append(row)
    return RegressionBuildResult(
        rows=tuple(rows),
        scheduled_observations=len(ordered),
        raw_eligible_count=len(rows),
        controlled_eligible_count=sum(row.control_returns is not None for row in rows),
        missing_reason_counts=dict(sorted(reasons.items())),
    )


def build_weak_daily_rows(
    observations: Sequence[WeakDailyObservation],
) -> RegressionBuildResult:
    """weak_daily_v1을 lag-1 program과 과거 20일 ADV로만 구성한다."""

    ordered = tuple(observations)
    dates = [item.trading_date for item in ordered]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("observation은 중복 없는 거래일 오름차순이어야 한다")
    if len({item.data_origin for item in ordered}) > 1:
        raise ValueError("actual과 fixture data origin을 한 study에서 혼합할 수 없다")
    reasons: Counter[str] = Counter()
    rows: list[PrefalsificationRegressionRow] = []
    for index in range(ADV_LOOKBACK_DAYS, len(ordered)):
        current = ordered[index]
        previous = ordered[index - PROGRAM_LAG_TRADING_DAYS]
        adv_window = ordered[index - ADV_LOOKBACK_DAYS : index]
        row = _build_one_weak_daily_row(current, previous, adv_window, reasons)
        if row is not None:
            rows.append(row)
    return RegressionBuildResult(
        rows=tuple(rows),
        scheduled_observations=len(ordered),
        raw_eligible_count=len(rows),
        controlled_eligible_count=sum(row.control_returns is not None for row in rows),
        missing_reason_counts=dict(sorted(reasons.items())),
    )


def _build_one_weak_daily_row(
    current: WeakDailyObservation,
    previous: WeakDailyObservation,
    adv_window: Sequence[WeakDailyObservation],
    reasons: Counter[str],
) -> PrefalsificationRegressionRow | None:
    required = {
        "PROGRAM_LAG1_MISSING": previous.program_net_buy_notional,
        "WEAK_OPEN_PRICE_MISSING": current.official_open_price,
        "OFFICIAL_CLOSE_MISSING": current.official_close_price,
    }
    for reason, value in required.items():
        if value.value is None:
            reasons[value.missing_reason or reason] += 1
            return None
    program = previous.program_net_buy_notional
    if program.unit != "KRW":
        reasons["PROGRAM_UNIT_INVALID"] += 1
        return None
    if program.available_at_utc is None or program.available_at_utc > current.market_open_utc:
        reasons["PROGRAM_LAG1_POST_CUTOFF"] += 1
        return None
    turnover_values: list[Decimal] = []
    for observation in adv_window:
        turnover = observation.total_turnover_notional
        if turnover.value is None or turnover.value <= 0:
            reasons[turnover.missing_reason or "ADV_WINDOW_MISSING"] += 1
            return None
        if turnover.unit != "KRW":
            reasons["ADV_UNIT_INVALID"] += 1
            return None
        if turnover.available_at_utc is None or turnover.available_at_utc > current.market_open_utc:
            reasons["ADV_POST_CUTOFF"] += 1
            return None
        turnover_values.append(turnover.value)
    open_price = current.official_open_price
    close_price = current.official_close_price
    if open_price.unit != "KRW" or close_price.unit != "KRW":
        reasons["WEAK_PRICE_UNIT_INVALID"] += 1
        return None
    if (
        open_price.event_time_utc != current.market_open_utc
        or close_price.event_time_utc != current.official_close_utc
        or open_price.available_at_utc is None
        or close_price.available_at_utc is None
        or open_price.available_at_utc < current.official_close_utc
        or close_price.available_at_utc < current.official_close_utc
    ):
        reasons["WEAK_PRICE_TIMING_INVALID"] += 1
        return None
    assert program.value is not None
    assert open_price.value is not None
    assert close_price.value is not None
    if open_price.value <= 0 or close_price.value <= 0:
        reasons["WEAK_PRICE_INVALID"] += 1
        return None
    adv20 = sum(turnover_values, Decimal("0")) / Decimal(ADV_LOOKBACK_DAYS)
    weak_return = Decimal(str(math.log(float(close_price.value / open_price.value))))
    controls = _weak_control_values(current, reasons)
    lineage = _weak_row_lineage(current, previous, adv_window)
    return PrefalsificationRegressionRow(
        trading_date=current.trading_date,
        x_program_lag1_adv20=program.value / adv20,
        y_signed_close_auction_notional_adv20=weak_return,
        auction_residual_return=weak_return,
        adv20_notional=adv20,
        control_returns=controls,
        input_record_ids=lineage,
    )


def _weak_control_values(
    current: WeakDailyObservation, reasons: Counter[str]
) -> Mapping[str, Decimal] | None:
    if set(current.control_returns) != set(CONTROL_FACTOR_NAMES):
        reasons["CONTROL_FACTOR_SCHEMA_INCOMPLETE"] += 1
        return None
    controls: dict[str, Decimal] = {}
    for name in CONTROL_FACTOR_NAMES:
        observation = current.control_returns[name]
        if observation.value is None:
            reasons[observation.missing_reason or f"CONTROL_MISSING:{name}"] += 1
            return None
        if observation.unit != "RETURN":
            reasons[f"CONTROL_UNIT_INVALID:{name}"] += 1
            return None
        if (
            observation.event_time_utc != current.official_close_utc
            or observation.available_at_utc is None
            or observation.available_at_utc < current.official_close_utc
        ):
            reasons[f"CONTROL_TIMING_INVALID:{name}"] += 1
            return None
        controls[name] = observation.value
    return controls


def _weak_row_lineage(
    current: WeakDailyObservation,
    previous: WeakDailyObservation,
    adv_window: Sequence[WeakDailyObservation],
) -> tuple[str, ...]:
    values: list[str] = []
    fields = [
        previous.program_net_buy_notional,
        current.official_open_price,
        current.official_close_price,
        *(item.total_turnover_notional for item in adv_window),
        *(current.control_returns.get(name) for name in CONTROL_FACTOR_NAMES),
    ]
    for study_value in fields:
        if (
            study_value is not None
            and study_value.input_record_id
            and study_value.input_record_id not in values
        ):
            values.append(study_value.input_record_id)
    return tuple(values)


def _build_one_row(
    current: PrefalsificationDailyObservation,
    previous: PrefalsificationDailyObservation,
    adv_window: Sequence[PrefalsificationDailyObservation],
    reasons: Counter[str],
) -> PrefalsificationRegressionRow | None:
    required = {
        "PROGRAM_LAG1_MISSING": previous.program_net_buy_notional,
        "PRE_AUCTION_REFERENCE_MISSING": current.pre_auction_reference_price,
        "OFFICIAL_CLOSE_MISSING": current.official_close_price,
        "CLOSE_AUCTION_TURNOVER_MISSING": current.close_auction_turnover_notional,
    }
    for reason, value in required.items():
        if value.value is None:
            reasons[value.missing_reason or reason] += 1
            return None
    if previous.program_net_buy_notional.unit != "KRW":
        reasons["PROGRAM_UNIT_INVALID"] += 1
        return None
    if any(
        value.unit != "KRW"
        for value in (
            current.pre_auction_reference_price,
            current.official_close_price,
            current.close_auction_turnover_notional,
        )
    ):
        reasons["PRICE_OR_AUCTION_UNIT_INVALID"] += 1
        return None
    turnover_values: list[Decimal] = []
    for observation in adv_window:
        turnover = observation.total_turnover_notional
        if turnover.value is None or turnover.value <= 0:
            reasons[turnover.missing_reason or "ADV_WINDOW_MISSING"] += 1
            return None
        if turnover.unit != "KRW":
            reasons["ADV_UNIT_INVALID"] += 1
            return None
        if turnover.available_at_utc is None or turnover.available_at_utc > current.auction_start_utc:
            reasons["ADV_POST_CUTOFF"] += 1
            return None
        turnover_values.append(turnover.value)
    program = previous.program_net_buy_notional
    if program.available_at_utc is None or program.available_at_utc > current.auction_start_utc:
        reasons["PROGRAM_LAG1_POST_CUTOFF"] += 1
        return None
    reference = current.pre_auction_reference_price
    if (
        reference.event_time_utc is None
        or reference.available_at_utc is None
        or reference.event_time_utc > current.auction_start_utc
        or reference.available_at_utc > current.auction_start_utc
        or current.auction_start_utc - reference.event_time_utc
        > PRE_AUCTION_MAX_AGE_SECONDS * _NANOSECONDS_PER_SECOND
    ):
        reasons["PRE_AUCTION_REFERENCE_NOT_CAUSAL_OR_STALE"] += 1
        return None
    close = current.official_close_price
    auction_turnover = current.close_auction_turnover_notional
    if (
        close.event_time_utc is None
        or close.available_at_utc is None
        or close.event_time_utc < current.auction_start_utc
        or close.available_at_utc < current.auction_end_utc
    ):
        reasons["OFFICIAL_CLOSE_TIMING_INVALID"] += 1
        return None
    if (
        auction_turnover.event_time_utc is None
        or auction_turnover.available_at_utc is None
        or auction_turnover.event_time_utc < current.auction_start_utc
        or auction_turnover.available_at_utc < current.auction_end_utc
    ):
        reasons["CLOSE_AUCTION_TURNOVER_TIMING_INVALID"] += 1
        return None
    assert program.value is not None
    assert reference.value is not None
    assert close.value is not None
    assert auction_turnover.value is not None
    if reference.value <= 0 or close.value <= 0 or auction_turnover.value < 0:
        reasons["PRICE_OR_AUCTION_NOTIONAL_INVALID"] += 1
        return None
    adv20 = sum(turnover_values, Decimal("0")) / Decimal(ADV_LOOKBACK_DAYS)
    if adv20 <= 0:
        reasons["ADV20_ZERO"] += 1
        return None
    auction_return = Decimal(str(math.log(float(close.value / reference.value))))
    direction = Decimal(int(auction_return > 0) - int(auction_return < 0))
    controls = _control_values(current, reasons)
    lineage = _row_lineage(current, previous, adv_window)
    return PrefalsificationRegressionRow(
        trading_date=current.trading_date,
        x_program_lag1_adv20=program.value / adv20,
        y_signed_close_auction_notional_adv20=(
            direction * auction_turnover.value / adv20
        ),
        auction_residual_return=auction_return,
        adv20_notional=adv20,
        control_returns=controls,
        input_record_ids=lineage,
    )


def _control_values(
    current: PrefalsificationDailyObservation, reasons: Counter[str]
) -> Mapping[str, Decimal] | None:
    if set(current.control_returns) != set(CONTROL_FACTOR_NAMES):
        reasons["CONTROL_FACTOR_SCHEMA_INCOMPLETE"] += 1
        return None
    controls: dict[str, Decimal] = {}
    for name in CONTROL_FACTOR_NAMES:
        observation = current.control_returns[name]
        if observation.value is None:
            reasons[observation.missing_reason or f"CONTROL_MISSING:{name}"] += 1
            return None
        if observation.unit != "RETURN":
            reasons[f"CONTROL_UNIT_INVALID:{name}"] += 1
            return None
        if (
            observation.event_time_utc != current.auction_end_utc
            or observation.available_at_utc is None
            or observation.available_at_utc < current.auction_end_utc
        ):
            reasons[f"CONTROL_TIMING_INVALID:{name}"] += 1
            return None
        controls[name] = observation.value
    return controls


def _row_lineage(
    current: PrefalsificationDailyObservation,
    previous: PrefalsificationDailyObservation,
    adv_window: Sequence[PrefalsificationDailyObservation],
) -> tuple[str, ...]:
    values: list[str] = []
    fields = [
        previous.program_net_buy_notional,
        current.pre_auction_reference_price,
        current.official_close_price,
        current.close_auction_turnover_notional,
        *(item.total_turnover_notional for item in adv_window),
        *(current.control_returns.get(name) for name in CONTROL_FACTOR_NAMES),
    ]
    for study_value in fields:
        if (
            study_value is not None
            and study_value.input_record_id
            and study_value.input_record_id not in values
        ):
            values.append(study_value.input_record_id)
    return tuple(values)


def fit_prefalsification_regression(
    rows: Sequence[PrefalsificationRegressionRow],
    *,
    variant: RegressionVariant,
    config: PrefalsificationStudyConfig | None = None,
) -> RegressionStatistics:
    resolved = config or PrefalsificationStudyConfig()
    if variant is RegressionVariant.RAW:
        selected = tuple(rows)
        x = np.asarray([float(row.x_program_lag1_adv20) for row in selected])
        y = np.asarray(
            [float(row.y_signed_close_auction_notional_adv20) for row in selected]
        )
        control_names: tuple[str, ...] = ()
        degrees_of_freedom = len(selected) - 2
    else:
        selected = tuple(row for row in rows if row.control_returns is not None)
        x, y = _common_factor_residuals(selected)
        control_names = CONTROL_FACTOR_NAMES
        degrees_of_freedom = len(selected) - 2 - len(CONTROL_FACTOR_NAMES)
    if len(selected) < MINIMUM_REGRESSION_OBSERVATIONS:
        raise ValueError("REGRESSION_SAMPLE_INSUFFICIENT")
    if degrees_of_freedom <= 0:
        raise ValueError("REGRESSION_DEGREES_OF_FREEDOM_INVALID")
    return _regression_statistics(
        x,
        y,
        variant=variant,
        control_names=control_names,
        degrees_of_freedom=degrees_of_freedom,
        config=resolved,
    )


def _common_factor_residuals(
    rows: Sequence[PrefalsificationRegressionRow],
) -> tuple[np.ndarray, np.ndarray]:
    controls = np.asarray(
        [
            [float(cast(Mapping[str, Decimal], row.control_returns)[name]) for name in CONTROL_FACTOR_NAMES]
            for row in rows
        ],
        dtype=float,
    )
    design = np.column_stack((np.ones(len(rows)), controls))
    if np.linalg.matrix_rank(design) != design.shape[1]:
        raise ValueError("CONTROL_MATRIX_RANK_DEFICIENT")
    x = np.asarray([float(row.x_program_lag1_adv20) for row in rows])
    y = np.asarray([float(row.y_signed_close_auction_notional_adv20) for row in rows])
    x_residual = x - design @ np.linalg.lstsq(design, x, rcond=None)[0]
    y_residual = y - design @ np.linalg.lstsq(design, y, rcond=None)[0]
    return x_residual, y_residual


def _regression_statistics(
    x: np.ndarray,
    y: np.ndarray,
    *,
    variant: RegressionVariant,
    control_names: tuple[str, ...],
    degrees_of_freedom: int,
    config: PrefalsificationStudyConfig,
) -> RegressionStatistics:
    if len(x) != len(y) or len(x) < MINIMUM_REGRESSION_OBSERVATIONS:
        raise ValueError("REGRESSION_SAMPLE_INSUFFICIENT")
    design = np.column_stack((np.ones(len(x)), x))
    if np.linalg.matrix_rank(design) != 2:
        raise ValueError("PROGRAM_FEATURE_ZERO_VARIANCE")
    coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
    fitted = design @ coefficients
    residuals = y - fitted
    hac_lags = max(1, int(math.floor(4 * (len(x) / 100) ** (2 / 9))))
    covariance = _newey_west_covariance(
        design, residuals, max_lags=hac_lags, degrees_of_freedom=degrees_of_freedom
    )
    standard_error = math.sqrt(max(float(covariance[1, 1]), 0.0))
    if standard_error == 0:
        raise ValueError("HAC_STANDARD_ERROR_ZERO")
    beta = float(coefficients[1])
    t_statistic = beta / standard_error
    analytic_p = math.erfc(abs(t_statistic) / math.sqrt(2))
    permutation_p = _date_permutation_p_value(
        x, y, permutations=config.permutations, seed=config.seed
    )
    ci = _block_bootstrap_beta_ci(
        x,
        y,
        resamples=config.bootstrap_resamples,
        block_days=config.bootstrap_block_days,
        seed=config.seed,
    )
    y_total = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - float(np.sum(residuals**2)) / y_total if y_total > 0 else 0.0
    x_std = float(np.std(x, ddof=1))
    y_std = float(np.std(y, ddof=1))
    standardized = beta * x_std / y_std if x_std > 0 and y_std > 0 else 0.0
    return RegressionStatistics(
        variant=variant,
        observation_count=len(x),
        hac_max_lags=hac_lags,
        intercept=Decimal(str(float(coefficients[0]))),
        beta=Decimal(str(beta)),
        hac_standard_error=Decimal(str(standard_error)),
        t_statistic=Decimal(str(t_statistic)),
        analytic_two_sided_p=Decimal(str(analytic_p)),
        permutation_p_value=Decimal(str(permutation_p)),
        block_bootstrap_ci=(Decimal(str(ci[0])), Decimal(str(ci[1]))),
        standardized_effect_size=Decimal(str(standardized)),
        r_squared=Decimal(str(r_squared)),
        control_names=control_names,
    )


def _newey_west_covariance(
    design: np.ndarray,
    residuals: np.ndarray,
    *,
    max_lags: int,
    degrees_of_freedom: int,
) -> np.ndarray:
    inverse_xx = np.linalg.inv(design.T @ design)
    weighted = design * residuals[:, None]
    meat = weighted.T @ weighted
    for lag in range(1, max_lags + 1):
        weight = 1.0 - lag / (max_lags + 1.0)
        gamma = weighted[lag:].T @ weighted[:-lag]
        meat += weight * (gamma + gamma.T)
    correction = len(design) / degrees_of_freedom
    return correction * inverse_xx @ meat @ inverse_xx


def _date_permutation_p_value(
    x: np.ndarray, y: np.ndarray, *, permutations: int, seed: int
) -> float:
    observed = abs(_simple_slope(x, y))
    rng = np.random.default_rng(seed)
    exceedances = 0
    for _ in range(permutations):
        permuted = rng.permutation(x)
        if abs(_simple_slope(permuted, y)) >= observed:
            exceedances += 1
    return (exceedances + 1) / (permutations + 1)


def _block_bootstrap_beta_ci(
    x: np.ndarray,
    y: np.ndarray,
    *,
    resamples: int,
    block_days: int,
    seed: int,
) -> tuple[float, float]:
    block_size = min(block_days, len(x))
    starts = np.arange(0, len(x) - block_size + 1)
    blocks_needed = math.ceil(len(x) / block_size)
    rng = np.random.default_rng(seed)
    slopes: list[float] = []
    for _ in range(resamples):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        indices = np.concatenate(
            [np.arange(start, start + block_size) for start in chosen]
        )[: len(x)]
        sampled_x = x[indices]
        if float(np.var(sampled_x)) == 0:
            continue
        slopes.append(_simple_slope(sampled_x, y[indices]))
    if len(slopes) < max(20, int(resamples * 0.9)):
        raise ValueError("BLOCK_BOOTSTRAP_NOT_IDENTIFIED")
    return float(np.quantile(slopes, 0.025)), float(np.quantile(slopes, 0.975))


def _simple_slope(x: np.ndarray, y: np.ndarray) -> float:
    centered_x = x - np.mean(x)
    denominator = float(centered_x @ centered_x)
    if denominator == 0:
        return 0.0
    return float(centered_x @ (y - np.mean(y)) / denominator)


def assess_prefalsification_statistics(
    statistics: RegressionStatistics,
) -> ModelAssessment:
    reasons: list[str] = []
    lower, upper = statistics.block_bootstrap_ci
    if statistics.beta <= 0:
        reasons.append("EXPECTED_POSITIVE_SIGN_NOT_MET")
    if abs(statistics.t_statistic) <= HAC_T_THRESHOLD:
        reasons.append("ABS_HAC_T_NOT_ABOVE_1_96")
    if lower <= 0 <= upper:
        reasons.append("BLOCK_BOOTSTRAP_CI_INCLUDES_ZERO")
    if statistics.permutation_p_value >= SIGNIFICANCE_LEVEL:
        reasons.append("DATE_PERMUTATION_P_NOT_BELOW_0_05")
    return ModelAssessment(
        statistics=statistics,
        verdict=(
            PrefalsificationVerdict.FALSIFY
            if reasons
            else PrefalsificationVerdict.PROCEED_TO_LIVE
        ),
        reasons=tuple(reasons),
    )


def run_prefalsification_study(
    observations: Sequence[PrefalsificationDailyObservation],
    config: PrefalsificationStudyConfig | None = None,
) -> PrefalsificationStudyResult:
    resolved = config or PrefalsificationStudyConfig()
    build = build_prefalsification_rows(observations)
    origin = (
        observations[0].data_origin
        if observations
        else PrefalsificationDataOrigin.KRX_HISTORICAL_ACTUAL
    )
    raw_model = _fit_assessment(build.rows, RegressionVariant.RAW, resolved)
    controlled_model = _fit_assessment(
        build.rows, RegressionVariant.COMMON_FACTOR_RESIDUAL, resolved
    )
    lineage = tuple(
        dict.fromkeys(record_id for row in build.rows for record_id in row.input_record_ids)
    )
    snapshot_hash = _observations_hash(observations)
    warnings = _study_warnings()
    if origin is PrefalsificationDataOrigin.SANITIZED_FIXTURE:
        return _study_result(
            PrefalsificationStatus.FIXTURE_ONLY,
            PrefalsificationVerdict.HOLD,
            ("SANITIZED_FIXTURE_NOT_DECISION_ELIGIBLE",),
            origin,
            build,
            raw_model,
            controlled_model,
            snapshot_hash,
            lineage,
            warnings,
        )
    if build.raw_eligible_count < MINIMUM_REGRESSION_OBSERVATIONS or controlled_model is None:
        return _study_result(
            PrefalsificationStatus.HOLD_DATA_UNAVAILABLE,
            PrefalsificationVerdict.HOLD,
            ("REQUIRED_PROGRAM_AUCTION_OR_CONTROL_DATA_UNAVAILABLE",),
            origin,
            build,
            raw_model,
            controlled_model,
            snapshot_hash,
            lineage,
            warnings,
        )
    if (
        build.scheduled_observations < MINIMUM_RAW_TRADING_DAYS
        or build.raw_eligible_count < MINIMUM_USABLE_OBSERVATIONS
        or build.controlled_eligible_count < MINIMUM_USABLE_OBSERVATIONS
    ):
        return _study_result(
            PrefalsificationStatus.HOLD_SAMPLE_INSUFFICIENT,
            PrefalsificationVerdict.HOLD,
            ("PRD_10_2_MINIMUM_3Y_SAMPLE_NOT_MET",),
            origin,
            build,
            raw_model,
            controlled_model,
            snapshot_hash,
            lineage,
            warnings,
        )
    assert raw_model is not None
    assert controlled_model is not None
    verdict = (
        PrefalsificationVerdict.FALSIFY
        if PrefalsificationVerdict.FALSIFY
        in (raw_model.verdict, controlled_model.verdict)
        else PrefalsificationVerdict.PROCEED_TO_LIVE
    )
    reasons = tuple(dict.fromkeys((*raw_model.reasons, *controlled_model.reasons)))
    if verdict is PrefalsificationVerdict.PROCEED_TO_LIVE:
        reasons = ("LIVE_COLLECTION_JUSTIFIED_NOT_PROMOTION",)
    return _study_result(
        PrefalsificationStatus.COMPLETED,
        verdict,
        reasons,
        origin,
        build,
        raw_model,
        controlled_model,
        snapshot_hash,
        lineage,
        warnings,
    )


def run_weak_daily_prefalsification_study(
    observations: Sequence[WeakDailyObservation],
    config: PrefalsificationStudyConfig | None = None,
    *,
    availability_audit: DataAvailabilityAudit | None = None,
) -> PrefalsificationStudyResult:
    """weak_daily_v1을 실행하되 strong 결론으로 승격시키지 않는다."""

    resolved = config or PrefalsificationStudyConfig()
    build = build_weak_daily_rows(observations)
    origin = (
        observations[0].data_origin
        if observations
        else PrefalsificationDataOrigin.KRX_HISTORICAL_ACTUAL
    )
    raw_model = _fit_assessment(build.rows, RegressionVariant.RAW, resolved)
    controlled_model = _fit_assessment(
        build.rows, RegressionVariant.COMMON_FACTOR_RESIDUAL, resolved
    )
    lineage = tuple(
        dict.fromkeys(record_id for row in build.rows for record_id in row.input_record_ids)
    )
    snapshot_hash = _weak_observations_hash(observations)
    warnings = _study_warnings(PrefalsificationModelVariant.WEAK_DAILY_V1)
    common = {
        "origin": origin,
        "build": build,
        "raw_model": raw_model,
        "controlled_model": controlled_model,
        "snapshot_hash": snapshot_hash,
        "lineage": lineage,
        "warnings": warnings,
        "model_variant": PrefalsificationModelVariant.WEAK_DAILY_V1,
        "availability_audit": availability_audit,
    }
    if origin is PrefalsificationDataOrigin.SANITIZED_FIXTURE:
        return _study_result(
            PrefalsificationStatus.FIXTURE_ONLY,
            PrefalsificationVerdict.HOLD,
            ("SANITIZED_FIXTURE_NOT_DECISION_ELIGIBLE",),
            **common,
        )
    if build.raw_eligible_count < MINIMUM_REGRESSION_OBSERVATIONS or controlled_model is None:
        missing_dataset_reasons = (
            tuple(
                f"MISSING_DATASET:{name}"
                for name in availability_audit.missing_required_datasets
            )
            if availability_audit is not None
            else ()
        )
        return _study_result(
            PrefalsificationStatus.HOLD_DATA_UNAVAILABLE,
            PrefalsificationVerdict.HOLD,
            (
                "WEAK_REQUIRED_PROGRAM_OR_CONTROL_DATA_UNAVAILABLE",
                *missing_dataset_reasons,
            ),
            **common,
        )
    if (
        build.scheduled_observations < MINIMUM_RAW_TRADING_DAYS
        or build.raw_eligible_count < MINIMUM_USABLE_OBSERVATIONS
        or build.controlled_eligible_count < MINIMUM_USABLE_OBSERVATIONS
    ):
        return _study_result(
            PrefalsificationStatus.HOLD_SAMPLE_INSUFFICIENT,
            PrefalsificationVerdict.HOLD,
            ("PRD_10_2_MINIMUM_3Y_SAMPLE_NOT_MET",),
            **common,
        )
    assert raw_model is not None
    assert controlled_model is not None
    verdict = (
        PrefalsificationVerdict.FALSIFY
        if PrefalsificationVerdict.FALSIFY
        in (raw_model.verdict, controlled_model.verdict)
        else PrefalsificationVerdict.PROCEED_TO_LIVE
    )
    reasons = tuple(dict.fromkeys((*raw_model.reasons, *controlled_model.reasons)))
    if verdict is PrefalsificationVerdict.PROCEED_TO_LIVE:
        reasons = ("WEAK_GREEN_LIGHT_FOR_LIVE_COLLECTION_ONLY",)
    else:
        reasons = (*reasons, "WEAK_FALSIFY_NOT_VALID_PIVOT_EVIDENCE")
    return _study_result(
        PrefalsificationStatus.COMPLETED,
        verdict,
        reasons,
        **common,
    )


def _fit_assessment(
    rows: Sequence[PrefalsificationRegressionRow],
    variant: RegressionVariant,
    config: PrefalsificationStudyConfig,
) -> ModelAssessment | None:
    try:
        statistics = fit_prefalsification_regression(
            rows, variant=variant, config=config
        )
    except ValueError:
        return None
    return assess_prefalsification_statistics(statistics)


def _study_result(
    status: PrefalsificationStatus,
    verdict: PrefalsificationVerdict,
    reasons: tuple[str, ...],
    origin: PrefalsificationDataOrigin,
    build: RegressionBuildResult,
    raw_model: ModelAssessment | None,
    controlled_model: ModelAssessment | None,
    snapshot_hash: str,
    lineage: tuple[str, ...],
    warnings: tuple[str, ...],
    model_variant: PrefalsificationModelVariant = (
        PrefalsificationModelVariant.STRONG_CLOSE_AUCTION_V1
    ),
    availability_audit: DataAvailabilityAudit | None = None,
) -> PrefalsificationStudyResult:
    return PrefalsificationStudyResult(
        status=status,
        verdict=verdict,
        reasons=reasons,
        data_origin=origin,
        scheduled_observations=build.scheduled_observations,
        raw_eligible_count=build.raw_eligible_count,
        controlled_eligible_count=build.controlled_eligible_count,
        missing_reason_counts=build.missing_reason_counts,
        raw_model=raw_model,
        controlled_model=controlled_model,
        data_snapshot_hash=snapshot_hash,
        input_record_ids=lineage,
        warnings=warnings,
        model_variant=model_variant,
        study_version=(
            WEAK_DAILY_STUDY_VERSION
            if model_variant is PrefalsificationModelVariant.WEAK_DAILY_V1
            else PREFALSIFICATION_STUDY_VERSION
        ),
        availability_audit=availability_audit,
    )


def audit_existing_krx_daily_data(data_root: Path) -> DataAvailabilityAudit:
    normalized_root = data_root / "normalized"
    files = tuple(sorted(normalized_root.rglob("*.parquet"))) if normalized_root.exists() else ()
    bar_count = 0
    symbols: set[str] = set()
    inspected: list[str] = []
    available: set[str] = set()
    hashes: list[str] = []
    for path in files:
        inspected.append(str(path))
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        if "krx_daily_ohlcv" in path.parts:
            table = pq.read_table(path, columns=["symbol"])
            bar_count += table.num_rows
            symbols.update(str(value) for value in table.column("symbol").to_pylist())
            available.add("krx_daily_ohlcv")
    required_paths = {
        "krx_program_trading_daily_12009": normalized_root
        / "krx_program_trading_daily_12009",
        "krx_close_auction_daily": normalized_root / "krx_close_auction_daily",
        "krx_kospi_index_daily": normalized_root / "krx_kospi_index_daily",
        "krx_semiconductor_index_daily": normalized_root
        / "krx_semiconductor_index_daily",
    }
    for name, path in required_paths.items():
        if path.exists() and any(path.rglob("*.parquet")):
            available.add(name)
    missing = tuple(name for name in required_paths if name not in available)
    snapshot_hash = hashlib.sha256(
        json.dumps(
            {"paths": inspected, "hashes": hashes}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return DataAvailabilityAudit(
        ohlcv_bar_count=bar_count,
        ohlcv_symbols=tuple(sorted(symbols)),
        available_datasets=tuple(sorted(available)),
        missing_required_datasets=missing,
        inspected_paths=tuple(inspected),
        data_snapshot_hash=snapshot_hash,
    )


def build_data_unavailable_result(
    audit: DataAvailabilityAudit,
) -> PrefalsificationStudyResult:
    reasons = tuple(f"MISSING_DATASET:{name}" for name in audit.missing_required_datasets)
    return PrefalsificationStudyResult(
        status=PrefalsificationStatus.HOLD_DATA_UNAVAILABLE,
        verdict=PrefalsificationVerdict.HOLD,
        reasons=reasons or ("REQUIRED_DATA_NOT_NORMALIZED",),
        data_origin=PrefalsificationDataOrigin.KRX_HISTORICAL_ACTUAL,
        scheduled_observations=0,
        raw_eligible_count=0,
        controlled_eligible_count=0,
        missing_reason_counts={},
        raw_model=None,
        controlled_model=None,
        data_snapshot_hash=audit.data_snapshot_hash,
        input_record_ids=(),
        warnings=_study_warnings(),
        availability_audit=audit,
    )


def load_prefalsification_observations_json(
    path: Path,
) -> tuple[PrefalsificationDailyObservation, ...]:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    origin = PrefalsificationDataOrigin(str(payload["data_origin"]))
    records = cast(list[dict[str, Any]], payload["observations"])
    return tuple(_observation_from_mapping(record, origin) for record in records)


def _observation_from_mapping(
    payload: Mapping[str, Any], origin: PrefalsificationDataOrigin
) -> PrefalsificationDailyObservation:
    controls_payload = cast(Mapping[str, Mapping[str, Any]], payload["control_returns"])
    return PrefalsificationDailyObservation(
        trading_date=date.fromisoformat(str(payload["trading_date"])),
        symbol=str(payload["symbol"]),
        auction_start_utc=int(payload["auction_start_utc"]),
        auction_end_utc=int(payload["auction_end_utc"]),
        program_net_buy_notional=_timed_value(
            cast(Mapping[str, Any], payload["program_net_buy_notional"])
        ),
        pre_auction_reference_price=_timed_value(
            cast(Mapping[str, Any], payload["pre_auction_reference_price"])
        ),
        official_close_price=_timed_value(
            cast(Mapping[str, Any], payload["official_close_price"])
        ),
        close_auction_turnover_notional=_timed_value(
            cast(Mapping[str, Any], payload["close_auction_turnover_notional"])
        ),
        total_turnover_notional=_timed_value(
            cast(Mapping[str, Any], payload["total_turnover_notional"])
        ),
        control_returns={
            name: _timed_value(value) for name, value in controls_payload.items()
        },
        data_origin=origin,
    )


def _timed_value(payload: Mapping[str, Any]) -> TimedStudyValue:
    raw_value = payload.get("value")
    return TimedStudyValue(
        value=Decimal(str(raw_value)) if raw_value is not None else None,
        event_time_utc=(
            int(payload["event_time_utc"])
            if payload.get("event_time_utc") is not None
            else None
        ),
        available_at_utc=(
            int(payload["available_at_utc"])
            if payload.get("available_at_utc") is not None
            else None
        ),
        source=str(payload.get("source", "")),
        unit=str(payload.get("unit", "")),
        input_record_id=(
            str(payload["input_record_id"])
            if payload.get("input_record_id") is not None
            else None
        ),
        missing_reason=(
            str(payload["missing_reason"])
            if payload.get("missing_reason") is not None
            else None
        ),
    )


def write_prefalsification_reports(
    result: PrefalsificationStudyResult,
    output_directory: Path,
    *,
    basename: str = "h1_prefalsification_study",
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / f"{basename}.json"
    markdown_path = output_directory / f"{basename}.md"
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown_report(result), encoding="utf-8")
    return json_path, markdown_path


def _markdown_report(result: PrefalsificationStudyResult) -> str:
    lines = [
        "# H1 사전반증 회귀 스터디",
        "",
        f"- 상태: `{result.status.value}`",
        f"- 판정: `{result.verdict.value}`",
        f"- 연구 버전: `{result.study_version}`",
        f"- model variant: `{result.model_variant.value}`",
        f"- 데이터 origin: `{result.data_origin.value}`",
        f"- 표본: scheduled={result.scheduled_observations}, "
        f"raw={result.raw_eligible_count}, controlled={result.controlled_eligible_count}",
        f"- snapshot hash: `{result.data_snapshot_hash}`",
        "- 용도: 라이브 수집 착수 여부의 사전반증 전용. 최종 승격·수익성 증거가 아님.",
        "- 주문 제출: 비활성화",
        "",
        "## 판정 사유",
        "",
        *(f"- `{reason}`" for reason in result.reasons),
        "",
        "## 봉인 회귀 스펙",
        "",
        "| 항 | 역할 | 원천·단위 | 변환·시각 | 룩어헤드 방지 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for spec in _field_specifications(result.model_variant):
        lines.append(
            f"| {spec.name} | {spec.role} | {spec.source} / {spec.raw_unit} | "
            f"{spec.transformation}; {spec.timing} | {spec.lookahead_rule} |"
        )
    lines.extend(["", "## 회귀 결과", ""])
    lines.extend(_model_markdown("Raw", result.raw_model))
    lines.extend(_model_markdown("공통요인 잔차", result.controlled_model))
    if result.availability_audit is not None:
        audit = result.availability_audit
        lines.extend(
            [
                "## 로컬 데이터 감사",
                "",
                f"- KRX OHLCV bars: {audit.ohlcv_bar_count}",
                f"- symbols: {list(audit.ohlcv_symbols)}",
                f"- available datasets: {list(audit.available_datasets)}",
                f"- missing datasets: {list(audit.missing_required_datasets)}",
                f"- dataset coverage: {dict(audit.dataset_coverage)}",
                f"- endpoint status: {dict(audit.endpoint_status)}",
                "",
            ]
        )
    lines.extend(["", "## 경고", ""])
    lines.extend(f"- {warning}" for warning in result.warnings)
    lines.append("")
    return "\n".join(lines)


def _model_markdown(label: str, assessment: ModelAssessment | None) -> list[str]:
    if assessment is None:
        return [f"### {label}", "", "`NOT_COMPUTABLE`", ""]
    stats = assessment.statistics
    return [
        f"### {label}",
        "",
        f"- verdict: `{assessment.verdict.value}`",
        f"- n={stats.observation_count}, beta={stats.beta}, HAC SE={stats.hac_standard_error}",
        f"- HAC t={stats.t_statistic}, permutation p={stats.permutation_p_value}",
        f"- block bootstrap 95% CI=[{stats.block_bootstrap_ci[0]}, "
        f"{stats.block_bootstrap_ci[1]}]",
        f"- standardized effect={stats.standardized_effect_size}, R²={stats.r_squared}",
        f"- reasons={list(assessment.reasons)}",
        "",
    ]


def _assessment_dict(assessment: ModelAssessment | None) -> dict[str, object] | None:
    if assessment is None:
        return None
    stats = assessment.statistics
    return {
        "variant": stats.variant.value,
        "verdict": assessment.verdict.value,
        "reasons": list(assessment.reasons),
        "observation_count": stats.observation_count,
        "hac_max_lags": stats.hac_max_lags,
        "intercept": str(stats.intercept),
        "beta": str(stats.beta),
        "hac_standard_error": str(stats.hac_standard_error),
        "t_statistic": str(stats.t_statistic),
        "analytic_two_sided_p": str(stats.analytic_two_sided_p),
        "permutation_p_value": str(stats.permutation_p_value),
        "block_bootstrap_ci": [
            str(stats.block_bootstrap_ci[0]),
            str(stats.block_bootstrap_ci[1]),
        ],
        "standardized_effect_size": str(stats.standardized_effect_size),
        "r_squared": str(stats.r_squared),
        "control_names": list(stats.control_names),
    }


def _availability_audit_dict(
    audit: DataAvailabilityAudit | None,
) -> dict[str, object] | None:
    if audit is None:
        return None
    return {
        "ohlcv_bar_count": audit.ohlcv_bar_count,
        "ohlcv_symbols": list(audit.ohlcv_symbols),
        "available_datasets": list(audit.available_datasets),
        "missing_required_datasets": list(audit.missing_required_datasets),
        "inspected_paths": list(audit.inspected_paths),
        "data_snapshot_hash": audit.data_snapshot_hash,
        "dataset_coverage": dict(audit.dataset_coverage),
        "endpoint_status": dict(audit.endpoint_status),
    }


def _observations_hash(observations: Sequence[PrefalsificationDailyObservation]) -> str:
    payload = []
    for item in observations:
        payload.append(
            {
                "date": item.trading_date.isoformat(),
                "origin": item.data_origin.value,
                "records": sorted(
                    record_id
                    for record_id in _observation_record_ids(item)
                    if record_id is not None
                ),
            }
        )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _weak_observations_hash(observations: Sequence[WeakDailyObservation]) -> str:
    payload = []
    for item in observations:
        payload.append(
            {
                "date": item.trading_date.isoformat(),
                "origin": item.data_origin.value,
                "records": sorted(
                    record_id
                    for record_id in _weak_observation_record_ids(item)
                    if record_id is not None
                ),
                "program_missing_reason": item.program_net_buy_notional.missing_reason,
            }
        )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _observation_record_ids(
    item: PrefalsificationDailyObservation,
) -> tuple[str | None, ...]:
    return (
        item.program_net_buy_notional.input_record_id,
        item.pre_auction_reference_price.input_record_id,
        item.official_close_price.input_record_id,
        item.close_auction_turnover_notional.input_record_id,
        item.total_turnover_notional.input_record_id,
        *(value.input_record_id for value in item.control_returns.values()),
    )


def _weak_observation_record_ids(item: WeakDailyObservation) -> tuple[str | None, ...]:
    return (
        item.program_net_buy_notional.input_record_id,
        item.official_open_price.input_record_id,
        item.official_close_price.input_record_id,
        item.total_turnover_notional.input_record_id,
        *(value.input_record_id for value in item.control_returns.values()),
    )


def _field_specifications(
    model_variant: PrefalsificationModelVariant,
) -> tuple[FieldSpecification, ...]:
    if model_variant is PrefalsificationModelVariant.WEAK_DAILY_V1:
        return WEAK_DAILY_FIELD_SPECIFICATIONS
    return PREFALSIFICATION_FIELD_SPECIFICATIONS


def _study_warnings(
    model_variant: PrefalsificationModelVariant = (
        PrefalsificationModelVariant.STRONG_CLOSE_AUCTION_V1
    ),
) -> tuple[str, ...]:
    if model_variant is PrefalsificationModelVariant.WEAK_DAILY_V1:
        return (
            "WEAK SPEC: Y=log(종가/시가)는 15:20→15:30 종가경매 잔여수익이 아니며 장중 노이즈를 포함한다.",
            "weak_daily_v1의 PROCEED_TO_LIVE는 라이브 수집 착수에 대한 약한 청신호일 뿐이다.",
            "weak_daily_v1의 FALSIFY는 false-negative 위험이 커서 피벗·개발 중단 근거로 신뢰하면 안 된다.",
            "일별 종일 program은 15:00~15:10 라이브 OFI/program flow와 시간 단위가 다르다.",
            "공통요인은 사후 nuisance control이며 거래 신호가 아니다.",
        )
    return (
        "일별 종일 프로그램 집계는 라이브 15:00~15:10 OFI/program flow와 다른 저해상도 proxy다.",
        "FALSIFY는 일별 proxy의 반증이며 장말 micro-dynamics의 부재를 증명하지 않는다.",
        "PROCEED_TO_LIVE는 6개월 라이브 수집 착수 정당화일 뿐 최종 승격·수익 보장이 아니다.",
        "공통요인 잔차 모델의 당일 종가수익률은 사후 nuisance control이며 거래 신호가 아니다.",
    )
