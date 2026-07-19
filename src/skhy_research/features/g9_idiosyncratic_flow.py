"""G9 특이 투자자 순매수와 공매도 보조 피처.

``IdioNB_000660(t-1)``은 봉인된 train 구간에서만 계수를 추정하고,
각 의사결정일 ``t``에 전 거래일 ``t-1``의 확정 순매수만 사용한다.
공매도 거래량은 t-1, 잔고는 공표 지연을 반영해 t-2만 사용한다.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as wall_time
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

import numpy as np

_SEOUL = ZoneInfo("Asia/Seoul")
_NS_PER_SECOND = 1_000_000_000


class InvestorFlowScope(StrEnum):
    SKHY_000660 = "000660"
    SAMSUNG_005930 = "005930"
    SEMICONDUCTOR = "SEMICONDUCTOR"
    MARKET = "MARKET"


@dataclass(frozen=True)
class InvestorNetBuyObservation:
    trading_date: date
    scope: InvestorFlowScope
    investor: str
    net_buy_notional: Decimal
    event_time_utc: int
    available_at_utc: int
    source: str
    input_record_id: str

    def __post_init__(self) -> None:
        if not self.investor.strip():
            raise ValueError("investor는 비어 있을 수 없다")
        if not self.net_buy_notional.is_finite():
            raise ValueError("순매수 대금은 유한해야 한다")
        if self.available_at_utc < self.event_time_utc:
            raise ValueError("available_at_utc는 event_time_utc보다 이를 수 없다")
        if not self.source.strip() or not self.input_record_id.strip():
            raise ValueError("수급 관측값에는 source와 input_record_id가 필요하다")


@dataclass(frozen=True)
class ShortSaleObservation:
    trading_date: date
    symbol: str
    short_volume: Decimal | None
    short_balance: Decimal | None
    event_time_utc: int
    source: str
    input_record_id: str
    volume_available_at_utc: int | None = None

    def __post_init__(self) -> None:
        if self.symbol != "000660":
            raise ValueError("공매도 보조 피처는 000660만 허용한다")
        if self.short_volume is None and self.short_balance is None:
            raise ValueError("공매도 거래량과 잔고가 모두 결측일 수 없다")
        for value in (self.short_volume, self.short_balance):
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError("공매도 값은 0 이상의 유한값이어야 한다")
        if (
            self.volume_available_at_utc is not None
            and self.volume_available_at_utc < self.event_time_utc
        ):
            raise ValueError("공매도 거래량 가용시각이 event보다 이를 수 없다")
        if not self.source.strip() or not self.input_record_id.strip():
            raise ValueError("공매도 관측값에는 source와 input_record_id가 필요하다")


@dataclass(frozen=True)
class G9ResidualizationConfig:
    investor: str
    train_start: date
    train_end: date
    minimum_train_observations: int = 30

    def __post_init__(self) -> None:
        if not self.investor.strip():
            raise ValueError("investor를 명시해야 한다")
        if self.train_start > self.train_end:
            raise ValueError("train_start는 train_end보다 늦을 수 없다")
        if self.minimum_train_observations < 4:
            raise ValueError("최소 train 관측수는 4 이상이어야 한다")


@dataclass(frozen=True)
class G9ResidualizationFit:
    investor: str
    train_start: date
    train_end: date
    observation_count: int
    b1_samsung: Decimal
    b2_semiconductor: Decimal
    b3_market: Decimal
    coefficient_snapshot_id: str
    input_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class G9DailyFeature:
    trading_date: date
    source_flow_date: date
    investor: str
    idio_nb_000660_lag1: Decimal
    short_volume_lag1: Decimal | None
    short_balance_lag2: Decimal | None
    short_balance_change_lag2: Decimal | None
    coefficient_snapshot_id: str
    available_at_utc: int
    input_record_ids: tuple[str, ...]
    missing_auxiliary_reasons: tuple[str, ...]


@dataclass(frozen=True)
class G9FeatureBuildResult:
    features: tuple[G9DailyFeature, ...]
    common_flow_trading_days: int
    fit: G9ResidualizationFit
    missing_reason_counts: Mapping[str, int]


def fit_g9_residualization(
    observations: Sequence[InvestorNetBuyObservation],
    config: G9ResidualizationConfig,
) -> G9ResidualizationFit:
    by_key = _flow_index(observations, config.investor)
    common_dates = _common_dates(by_key)
    train_dates = tuple(
        day for day in common_dates if config.train_start <= day <= config.train_end
    )
    if len(train_dates) < config.minimum_train_observations:
        raise ValueError(
            "G9_TRAIN_SAMPLE_INSUFFICIENT:"
            f"required={config.minimum_train_observations},actual={len(train_dates)}"
        )

    target = np.asarray(
        [float(by_key[(day, InvestorFlowScope.SKHY_000660)].net_buy_notional) for day in train_dates]
    )
    controls = np.asarray(
        [
            [
                float(by_key[(day, InvestorFlowScope.SAMSUNG_005930)].net_buy_notional),
                float(by_key[(day, InvestorFlowScope.SEMICONDUCTOR)].net_buy_notional),
                float(by_key[(day, InvestorFlowScope.MARKET)].net_buy_notional),
            ]
            for day in train_dates
        ]
    )
    if int(np.linalg.matrix_rank(controls)) < controls.shape[1]:
        raise ValueError("G9_TRAIN_CONTROL_MATRIX_RANK_DEFICIENT")
    coefficients = np.linalg.lstsq(controls, target, rcond=None)[0]
    if not np.all(np.isfinite(coefficients)):
        raise ValueError("G9_TRAIN_COEFFICIENT_NOT_FINITE")

    record_ids = tuple(
        by_key[(day, scope)].input_record_id
        for day in train_dates
        for scope in InvestorFlowScope
    )
    snapshot_payload = {
        "model": "G9_IDIO_NB_NO_INTERCEPT_V1",
        "investor": config.investor,
        "train_start": config.train_start.isoformat(),
        "train_end": config.train_end.isoformat(),
        "record_ids": record_ids,
    }
    digest = hashlib.sha256(
        json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return G9ResidualizationFit(
        investor=config.investor,
        train_start=config.train_start,
        train_end=config.train_end,
        observation_count=len(train_dates),
        b1_samsung=Decimal(str(coefficients[0])),
        b2_semiconductor=Decimal(str(coefficients[1])),
        b3_market=Decimal(str(coefficients[2])),
        coefficient_snapshot_id=f"g9-coefficients:{digest}",
        input_record_ids=record_ids,
    )


def build_g9_features(
    observations: Sequence[InvestorNetBuyObservation],
    fit: G9ResidualizationFit,
    *,
    short_sale_observations: Sequence[ShortSaleObservation] = (),
) -> G9FeatureBuildResult:
    by_key = _flow_index(observations, fit.investor)
    common_dates = _common_dates(by_key)
    short_by_date = {item.trading_date: item for item in short_sale_observations}
    if len(short_by_date) != len(short_sale_observations):
        raise ValueError("MDCSTAT300_DUPLICATE_TRADING_DATE")

    features: list[G9DailyFeature] = []
    missing = Counter[str]()
    for index in range(1, len(common_dates)):
        trading_date = common_dates[index]
        source_date = common_dates[index - 1]
        decision_utc = _seoul_nanos(trading_date, wall_time(9, 0))
        source_rows = tuple(by_key[(source_date, scope)] for scope in InvestorFlowScope)
        if any(item.available_at_utc > decision_utc for item in source_rows):
            missing["INVESTOR_FLOW_LAG1_POST_CUTOFF"] += 1
            continue

        target, samsung, semiconductor, market = source_rows
        idio = (
            target.net_buy_notional
            - fit.b1_samsung * samsung.net_buy_notional
            - fit.b2_semiconductor * semiconductor.net_buy_notional
            - fit.b3_market * market.net_buy_notional
        )
        auxiliary_reasons: list[str] = []
        short_ids: list[str] = []
        volume: Decimal | None = None
        balance: Decimal | None = None
        balance_change: Decimal | None = None
        volume_row = short_by_date.get(source_date)
        if volume_row is None or volume_row.short_volume is None:
            auxiliary_reasons.append("SHORT_VOLUME_T_MINUS_1_UNAVAILABLE")
        elif (
            volume_row.volume_available_at_utc is not None
            and volume_row.volume_available_at_utc > decision_utc
        ):
            auxiliary_reasons.append("SHORT_VOLUME_T_MINUS_1_POST_CUTOFF")
        else:
            volume = volume_row.short_volume
            short_ids.append(volume_row.input_record_id)

        if index < 2:
            auxiliary_reasons.append("SHORT_BALANCE_T_MINUS_2_WARMUP")
        else:
            balance_date = common_dates[index - 2]
            balance_row = short_by_date.get(balance_date)
            if balance_row is None or balance_row.short_balance is None:
                auxiliary_reasons.append("SHORT_BALANCE_T_MINUS_2_UNAVAILABLE")
            else:
                balance = balance_row.short_balance
                short_ids.append(balance_row.input_record_id)
                if index < 3:
                    auxiliary_reasons.append("SHORT_BALANCE_CHANGE_WARMUP")
                else:
                    prior_row = short_by_date.get(common_dates[index - 3])
                    if prior_row is None or prior_row.short_balance is None:
                        auxiliary_reasons.append("SHORT_BALANCE_CHANGE_PRIOR_UNAVAILABLE")
                    else:
                        balance_change = balance - prior_row.short_balance
                        short_ids.append(prior_row.input_record_id)

        missing.update(auxiliary_reasons)
        features.append(
            G9DailyFeature(
                trading_date=trading_date,
                source_flow_date=source_date,
                investor=fit.investor,
                idio_nb_000660_lag1=idio,
                short_volume_lag1=volume,
                short_balance_lag2=balance,
                short_balance_change_lag2=balance_change,
                coefficient_snapshot_id=fit.coefficient_snapshot_id,
                available_at_utc=max(item.available_at_utc for item in source_rows),
                input_record_ids=tuple(
                    dict.fromkeys(
                        (
                            *(item.input_record_id for item in source_rows),
                            *short_ids,
                            fit.coefficient_snapshot_id,
                        )
                    )
                ),
                missing_auxiliary_reasons=tuple(auxiliary_reasons),
            )
        )
    return G9FeatureBuildResult(
        features=tuple(features),
        common_flow_trading_days=len(common_dates),
        fit=fit,
        missing_reason_counts=dict(sorted(missing.items())),
    )


def _flow_index(
    observations: Sequence[InvestorNetBuyObservation], investor: str
) -> dict[tuple[date, InvestorFlowScope], InvestorNetBuyObservation]:
    result: dict[tuple[date, InvestorFlowScope], InvestorNetBuyObservation] = {}
    for item in observations:
        if item.investor != investor:
            continue
        key = (item.trading_date, item.scope)
        if key in result:
            raise ValueError(
                f"G9_DUPLICATE_FLOW:{item.trading_date.isoformat()}:{item.scope.value}:{investor}"
            )
        result[key] = item
    return result


def _common_dates(
    by_key: Mapping[tuple[date, InvestorFlowScope], InvestorNetBuyObservation],
) -> tuple[date, ...]:
    date_sets = [
        {day for day, item_scope in by_key if item_scope is scope}
        for scope in InvestorFlowScope
    ]
    if not date_sets:
        return ()
    return tuple(sorted(set.intersection(*date_sets)))


def _seoul_nanos(day: date, clock_time: wall_time) -> int:
    value = datetime.combine(day, clock_time, tzinfo=_SEOUL).timestamp()
    return int(value * _NS_PER_SECOND)
