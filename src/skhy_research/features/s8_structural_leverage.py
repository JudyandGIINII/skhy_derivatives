"""S8 구조적 레버리지 앙상블의 계산·결측·계보 골격.

NAV, 상장좌수, 설정환매(ΔShares), NAV 프리미엄, 추적오차를 서로
다른 피처로 유지한다. 결측을 0·전진채움·유사상품으로 대체하지 않고,
각 피처에 missing reason과 입력 record id를 보존한다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as wall_time
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

_SEOUL = ZoneInfo("Asia/Seoul")
_NS_PER_SECOND = 1_000_000_000
S8_MINIMUM_TRADING_DAYS = 120


class S8DataOrigin(StrEnum):
    KRX_ACTUAL = "KRX_ACTUAL"
    SANITIZED_FIXTURE = "SANITIZED_FIXTURE"


class S8Status(StrEnum):
    HOLD_SAMPLE_INSUFFICIENT = "HOLD_SAMPLE_INSUFFICIENT"
    FIXTURE_ONLY = "FIXTURE_ONLY"
    READY_FOR_UNSEALED_RESEARCH = "READY_FOR_UNSEALED_RESEARCH"


@dataclass(frozen=True)
class S8TimedValue:
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
                raise ValueError("S8 결측값에 missing_reason이 필요하다")
            if self.input_record_id is not None:
                raise ValueError("S8 결측값에 input_record_id를 붙일 수 없다")
            return
        if not self.value.is_finite():
            raise ValueError("S8 관측값은 유한해야 한다")
        if self.event_time_utc is None or self.available_at_utc is None:
            raise ValueError("S8 관측값에 시각이 필요하다")
        if self.available_at_utc < self.event_time_utc:
            raise ValueError("S8 available_at은 event_time보다 이를 수 없다")
        if not self.source.strip() or not self.unit.strip() or not self.input_record_id:
            raise ValueError("S8 관측값에 source·unit·record id가 필요하다")
        if self.missing_reason is not None:
            raise ValueError("S8 관측값에 missing_reason을 둘 수 없다")


@dataclass(frozen=True)
class S8DailyObservation:
    trading_date: date
    product_symbol: str
    nav_per_share: S8TimedValue
    listed_shares: S8TimedValue
    market_close: S8TimedValue
    product_return: S8TimedValue
    underlying_return: S8TimedValue
    leverage_multiple: Decimal
    data_origin: S8DataOrigin

    def __post_init__(self) -> None:
        if not self.product_symbol.strip():
            raise ValueError("S8 product_symbol은 빈 값일 수 없다")
        if not self.leverage_multiple.is_finite() or self.leverage_multiple == 0:
            raise ValueError("S8 leverage_multiple은 0이 아닌 유한값이어야 한다")


@dataclass(frozen=True)
class S8FeatureValue:
    value: Decimal | None
    unit: str
    source_trading_date: date | None
    available_at_utc: int | None
    input_record_ids: tuple[str, ...]
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            if not self.missing_reason:
                raise ValueError("S8 피처 결측에 missing_reason이 필요하다")
            if self.input_record_ids:
                raise ValueError("S8 결측 피처에 가짜 계보를 붙일 수 없다")
            return
        if not self.value.is_finite():
            raise ValueError("S8 피처는 유한해야 한다")
        if self.source_trading_date is None or self.available_at_utc is None:
            raise ValueError("S8 피처에 원천 일자·가용시각이 필요하다")
        if not self.input_record_ids:
            raise ValueError("S8 피처에 입력 계보가 필요하다")
        if self.missing_reason is not None:
            raise ValueError("S8 관측 피처에 missing_reason을 둘 수 없다")


@dataclass(frozen=True)
class S8DailyFeatures:
    trading_date: date
    product_symbol: str
    nav: S8FeatureValue
    listed_shares: S8FeatureValue
    delta_shares: S8FeatureValue
    nav_premium: S8FeatureValue
    tracking_error: S8FeatureValue


@dataclass(frozen=True)
class S8SkeletonResult:
    status: S8Status
    reasons: tuple[str, ...]
    observation_count: int
    feature_count: int
    features: tuple[S8DailyFeatures, ...]
    missing_reason_counts: Mapping[str, int]
    input_record_ids: tuple[str, ...]
    minimum_required_trading_days: int = S8_MINIMUM_TRADING_DAYS
    paper_only: bool = True
    order_submission_enabled: bool = False
    hyperparameter_search_enabled: bool = False
    sealed_test_enabled: bool = False
    proxy_or_synthetic_performance_allowed: bool = False


def build_s8_structural_leverage_skeleton(
    observations: Sequence[S8DailyObservation],
) -> S8SkeletonResult:
    ordered = tuple(sorted(observations, key=lambda item: item.trading_date))
    if len({(item.product_symbol, item.trading_date) for item in ordered}) != len(ordered):
        raise ValueError("S8 상품·거래일 관측값이 중복됐다")
    origins = {item.data_origin for item in ordered}
    if len(origins) > 1:
        raise ValueError("S8 actual과 fixture 원천을 혼합할 수 없다")

    by_symbol: dict[str, list[S8DailyObservation]] = {}
    for item in ordered:
        by_symbol.setdefault(item.product_symbol, []).append(item)
    features: list[S8DailyFeatures] = []
    missing = Counter[str]()
    lineage: list[str] = []
    for symbol, items in sorted(by_symbol.items()):
        for index in range(1, len(items)):
            trading_date = items[index].trading_date
            source = items[index - 1]
            prior = items[index - 2] if index >= 2 else None
            cutoff = _seoul_nanos(trading_date, wall_time(9, 0))
            row = S8DailyFeatures(
                trading_date=trading_date,
                product_symbol=symbol,
                nav=_direct_feature(source.nav_per_share, source.trading_date, cutoff, "NAV"),
                listed_shares=_direct_feature(
                    source.listed_shares, source.trading_date, cutoff, "LISTED_SHARES"
                ),
                delta_shares=_delta_shares_feature(source, prior, cutoff),
                nav_premium=_ratio_feature(
                    source.market_close,
                    source.nav_per_share,
                    source.trading_date,
                    cutoff,
                    name="NAV_PREMIUM",
                ),
                tracking_error=_tracking_error_feature(source, cutoff),
            )
            features.append(row)
            for feature in (
                row.nav,
                row.listed_shares,
                row.delta_shares,
                row.nav_premium,
                row.tracking_error,
            ):
                if feature.missing_reason is not None:
                    missing[feature.missing_reason] += 1
                lineage.extend(feature.input_record_ids)

    actual_count = len(ordered)
    if origins == {S8DataOrigin.SANITIZED_FIXTURE}:
        status = S8Status.FIXTURE_ONLY
        reasons = ("SANITIZED_FIXTURE_NOT_PERFORMANCE_EVIDENCE",)
    elif actual_count < S8_MINIMUM_TRADING_DAYS:
        status = S8Status.HOLD_SAMPLE_INSUFFICIENT
        reasons = (
            "PRD_10_2_H1_MINIMUM_120_TRADING_DAYS_NOT_MET",
            f"ACTUAL_TRADING_DAYS:{actual_count}",
        )
    else:
        status = S8Status.READY_FOR_UNSEALED_RESEARCH
        reasons = ("SKELETON_ONLY_NO_HYPERPARAMETER_OR_SEALED_TEST",)
    return S8SkeletonResult(
        status=status,
        reasons=reasons,
        observation_count=actual_count,
        feature_count=len(features),
        features=tuple(features),
        missing_reason_counts=dict(sorted(missing.items())),
        input_record_ids=tuple(dict.fromkeys(lineage)),
    )


def _direct_feature(
    value: S8TimedValue,
    source_date: date,
    cutoff: int,
    name: str,
) -> S8FeatureValue:
    if value.value is None:
        return _missing(f"{name}:{value.missing_reason}", value.unit)
    assert value.available_at_utc is not None
    assert value.input_record_id is not None
    if value.available_at_utc > cutoff:
        return _missing(f"{name}_T_MINUS_1_POST_CUTOFF", value.unit)
    return S8FeatureValue(
        value=value.value,
        unit=value.unit,
        source_trading_date=source_date,
        available_at_utc=value.available_at_utc,
        input_record_ids=(value.input_record_id,),
    )


def _delta_shares_feature(
    source: S8DailyObservation,
    prior: S8DailyObservation | None,
    cutoff: int,
) -> S8FeatureValue:
    if prior is None:
        return _missing("DELTA_SHARES_WARMUP", "SHARES")
    current = source.listed_shares
    previous = prior.listed_shares
    if current.value is None:
        return _missing(f"DELTA_SHARES:{current.missing_reason}", "SHARES")
    if previous.value is None:
        return _missing(f"DELTA_SHARES_PRIOR:{previous.missing_reason}", "SHARES")
    assert current.available_at_utc is not None and previous.available_at_utc is not None
    assert current.input_record_id is not None and previous.input_record_id is not None
    available = max(current.available_at_utc, previous.available_at_utc)
    if available > cutoff:
        return _missing("DELTA_SHARES_T_MINUS_1_POST_CUTOFF", "SHARES")
    return S8FeatureValue(
        value=current.value - previous.value,
        unit="SHARES",
        source_trading_date=source.trading_date,
        available_at_utc=available,
        input_record_ids=(current.input_record_id, previous.input_record_id),
    )


def _ratio_feature(
    numerator: S8TimedValue,
    denominator: S8TimedValue,
    source_date: date,
    cutoff: int,
    *,
    name: str,
) -> S8FeatureValue:
    if numerator.value is None:
        return _missing(f"{name}:{numerator.missing_reason}", "RATE")
    if denominator.value is None:
        return _missing(f"{name}:{denominator.missing_reason}", "RATE")
    if denominator.value == 0:
        return _missing(f"{name}_DENOMINATOR_ZERO", "RATE")
    assert numerator.available_at_utc is not None and denominator.available_at_utc is not None
    assert numerator.input_record_id is not None and denominator.input_record_id is not None
    available = max(numerator.available_at_utc, denominator.available_at_utc)
    if available > cutoff:
        return _missing(f"{name}_T_MINUS_1_POST_CUTOFF", "RATE")
    return S8FeatureValue(
        value=numerator.value / denominator.value - Decimal("1"),
        unit="RATE",
        source_trading_date=source_date,
        available_at_utc=available,
        input_record_ids=(numerator.input_record_id, denominator.input_record_id),
    )


def _tracking_error_feature(
    source: S8DailyObservation,
    cutoff: int,
) -> S8FeatureValue:
    product = source.product_return
    underlying = source.underlying_return
    if product.value is None:
        return _missing(f"TRACKING_ERROR:{product.missing_reason}", "RETURN")
    if underlying.value is None:
        return _missing(f"TRACKING_ERROR:{underlying.missing_reason}", "RETURN")
    assert product.available_at_utc is not None and underlying.available_at_utc is not None
    assert product.input_record_id is not None and underlying.input_record_id is not None
    available = max(product.available_at_utc, underlying.available_at_utc)
    if available > cutoff:
        return _missing("TRACKING_ERROR_T_MINUS_1_POST_CUTOFF", "RETURN")
    return S8FeatureValue(
        value=product.value - source.leverage_multiple * underlying.value,
        unit="RETURN",
        source_trading_date=source.trading_date,
        available_at_utc=available,
        input_record_ids=(product.input_record_id, underlying.input_record_id),
    )


def _missing(reason: str, unit: str) -> S8FeatureValue:
    return S8FeatureValue(
        value=None,
        unit=unit,
        source_trading_date=None,
        available_at_utc=None,
        input_record_ids=(),
        missing_reason=reason,
    )


def _seoul_nanos(day: date, clock_time: wall_time) -> int:
    return int(datetime.combine(day, clock_time, tzinfo=_SEOUL).timestamp() * _NS_PER_SECOND)
