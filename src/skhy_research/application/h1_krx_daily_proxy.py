"""무료 KRX 일별정보로 만드는 H1 연구용 축소 feature.

이 경로는 KRX의 통상 직전일 데이터를 과거 확인·크로스체크·백필에만 사용한다.
15:10 장중 H1과 데이터 해상도·model version·promotion scope를 공유하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Self

from skhy_research.application.leverage_universe_discovery import DiscoveredLeveragedProduct
from skhy_research.features.h1_close_pressure.close_pressure import (
    ClosePressureResult,
    FundContribution,
    estimated_close_pressure,
)
from skhy_research.features.h1_close_pressure.theoretical_exposure import (
    theoretical_delta_exposure,
)

KRX_DAILY_PROXY_MODEL_VERSION = "h1_krx_daily_proxy_reduced_v1"
KRX_DAILY_PROXY_DATA_RESOLUTION = "daily-proxy"
KRX_DAILY_PROXY_PROMOTION_SCOPE = "h1-daily-proxy-research-only"
KRX_DAILY_PROXY_AVAILABILITY_POLICY = "prior-basis-date-and-received-before-as-of"


class KrxDailyProxyInputError(ValueError):
    """무료 KRX 일별 proxy 입력이 결측·미래 데이터·잘못된 단위를 포함할 때."""


@dataclass(frozen=True)
class KrxDailyProxyFundInput:
    fund_id: str
    beta: Decimal
    nav_or_iv: Decimal
    listed_shares: Decimal
    kappa: Decimal
    basis_date: date
    received_at_utc: int
    input_record_ids: tuple[str, ...]

    @classmethod
    def from_discovered_product(
        cls,
        product: DiscoveredLeveragedProduct,
        *,
        kappa: Decimal,
        received_at_utc: int,
        input_record_ids: tuple[str, ...],
    ) -> Self:
        if product.nav_or_indicative_value is None:
            raise KrxDailyProxyInputError(f"fund_id={product.instrument_id}의 NAV/IV가 없음")
        if product.listed_shares is None:
            raise KrxDailyProxyInputError(f"fund_id={product.instrument_id}의 상장좌수가 없음")
        return cls(
            fund_id=product.instrument_id,
            beta=product.leverage_factor,
            nav_or_iv=product.nav_or_indicative_value,
            listed_shares=product.listed_shares,
            kappa=kappa,
            basis_date=product.basis_date,
            received_at_utc=received_at_utc,
            input_record_ids=input_record_ids,
        )


@dataclass(frozen=True)
class KrxDailyProxyMarketInput:
    basis_date: date
    previous_close: Decimal
    close: Decimal
    turnover_notional_20d: tuple[Decimal, ...]
    received_at_utc: int
    input_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class KrxDailyProxyFundFeature:
    fund_id: str
    beta: Decimal
    listed_notional_proxy: Decimal
    theoretical_delta_exposure: Decimal
    kappa: Decimal
    model_version: str = KRX_DAILY_PROXY_MODEL_VERSION
    data_resolution: str = KRX_DAILY_PROXY_DATA_RESOLUTION
    promotion_scope: str = KRX_DAILY_PROXY_PROMOTION_SCOPE
    promotion_eligible: bool = False


@dataclass(frozen=True)
class KrxDailyProxyFeatureSet:
    basis_date: date
    signal_date: date
    underlying_daily_return_proxy: Decimal
    underlying_20d_adv_notional: Decimal
    fund_features: tuple[KrxDailyProxyFundFeature, ...]
    close_pressure: ClosePressureResult
    input_record_ids: tuple[str, ...]
    availability_policy: str = KRX_DAILY_PROXY_AVAILABILITY_POLICY
    model_version: str = KRX_DAILY_PROXY_MODEL_VERSION
    data_resolution: str = KRX_DAILY_PROXY_DATA_RESOLUTION
    promotion_scope: str = KRX_DAILY_PROXY_PROMOTION_SCOPE
    promotion_eligible: bool = False


def build_krx_daily_proxy_feature(
    fund_inputs: list[KrxDailyProxyFundInput],
    market_input: KrxDailyProxyMarketInput,
    *,
    signal_date: date,
    as_of_time_utc: int,
) -> KrxDailyProxyFeatureSet:
    """직전 기준일 KRX 데이터만으로 daily-proxy close pressure를 만든다."""

    if not fund_inputs:
        raise KrxDailyProxyInputError("fund_inputs는 비어 있을 수 없다")
    _assert_market_input_available(market_input, signal_date, as_of_time_utc)

    daily_return = calculate_daily_return_proxy(
        market_input.previous_close, market_input.close
    )
    adv_notional = calculate_20d_adv_notional(market_input.turnover_notional_20d)

    features: list[KrxDailyProxyFundFeature] = []
    contributions: list[FundContribution] = []
    lineage_ids = _LineageIds()
    lineage_ids.add_many(market_input.input_record_ids, owner="underlying-market")
    seen_fund_ids: set[str] = set()

    for item in fund_inputs:
        _assert_fund_input_available(item, market_input.basis_date, signal_date, as_of_time_utc)
        if item.fund_id in seen_fund_ids:
            raise KrxDailyProxyInputError(f"fund_id 중복: {item.fund_id}")
        seen_fund_ids.add(item.fund_id)
        lineage_ids.add_many(item.input_record_ids, owner=item.fund_id)

        if item.nav_or_iv <= 0:
            raise KrxDailyProxyInputError(f"fund_id={item.fund_id}의 NAV/IV는 0보다 커야 한다")
        if item.listed_shares <= 0:
            raise KrxDailyProxyInputError(
                f"fund_id={item.fund_id}의 listed_shares는 0보다 커야 한다"
            )

        listed_notional = item.nav_or_iv * item.listed_shares
        exposure = theoretical_delta_exposure(item.beta, listed_notional, daily_return)
        features.append(
            KrxDailyProxyFundFeature(
                fund_id=item.fund_id,
                beta=item.beta,
                listed_notional_proxy=listed_notional,
                theoretical_delta_exposure=exposure,
                kappa=item.kappa,
            )
        )
        contributions.append(
            FundContribution(
                fund_id=item.fund_id,
                theoretical_delta_exposure=exposure,
                kappa=item.kappa,
                observable_flow_adjustment=None,
            )
        )

    base_pressure = estimated_close_pressure(contributions, adv_notional)
    close_pressure = ClosePressureResult(
        value=base_pressure.value,
        model_version=KRX_DAILY_PROXY_MODEL_VERSION,
        missing_flow_fund_ids=base_pressure.missing_flow_fund_ids,
        data_resolution=KRX_DAILY_PROXY_DATA_RESOLUTION,
        promotion_scope=KRX_DAILY_PROXY_PROMOTION_SCOPE,
        promotion_eligible=False,
    )
    return KrxDailyProxyFeatureSet(
        basis_date=market_input.basis_date,
        signal_date=signal_date,
        underlying_daily_return_proxy=daily_return,
        underlying_20d_adv_notional=adv_notional,
        fund_features=tuple(features),
        close_pressure=close_pressure,
        input_record_ids=lineage_ids.values,
    )


def calculate_daily_return_proxy(previous_close: Decimal, close: Decimal) -> Decimal:
    if previous_close <= 0 or close <= 0:
        raise KrxDailyProxyInputError("일별 수익률 proxy의 전일·당일 종가는 0보다 커야 한다")
    return close / previous_close - Decimal("1")


def calculate_20d_adv_notional(turnovers: tuple[Decimal, ...]) -> Decimal:
    if len(turnovers) != 20:
        raise KrxDailyProxyInputError(
            f"20일 ADV에는 정확히 20개 거래대금이 필요하다: count={len(turnovers)}"
        )
    if any(value <= 0 for value in turnovers):
        raise KrxDailyProxyInputError("20일 ADV 거래대금은 모두 0보다 커야 한다")
    return sum(turnovers, Decimal("0")) / Decimal("20")


def _assert_market_input_available(
    item: KrxDailyProxyMarketInput, signal_date: date, as_of_time_utc: int
) -> None:
    if item.basis_date >= signal_date:
        raise KrxDailyProxyInputError(
            f"market basis_date={item.basis_date}는 signal_date={signal_date}보다 이전이어야 한다"
        )
    if item.received_at_utc >= as_of_time_utc:
        raise KrxDailyProxyInputError("market 데이터가 as_of 시점 전에 수신되지 않았다")
    if not item.input_record_ids:
        raise KrxDailyProxyInputError("underlying market lineage record ID가 필요하다")


def _assert_fund_input_available(
    item: KrxDailyProxyFundInput,
    market_basis_date: date,
    signal_date: date,
    as_of_time_utc: int,
) -> None:
    if item.basis_date != market_basis_date:
        raise KrxDailyProxyInputError(
            f"fund_id={item.fund_id}와 market의 basis_date가 다르다: "
            f"fund={item.basis_date}, market={market_basis_date}"
        )
    if item.basis_date >= signal_date:
        raise KrxDailyProxyInputError(
            f"fund_id={item.fund_id}의 basis_date는 signal_date보다 이전이어야 한다"
        )
    if item.received_at_utc >= as_of_time_utc:
        raise KrxDailyProxyInputError(
            f"fund_id={item.fund_id} 데이터가 as_of 시점 전에 수신되지 않았다"
        )
    if not item.input_record_ids:
        raise KrxDailyProxyInputError(f"fund_id={item.fund_id}의 lineage record ID가 필요하다")


class _LineageIds:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._values: list[str] = []

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(self._values)

    def add_many(self, values: tuple[str, ...], *, owner: str) -> None:
        if not values:
            raise KrxDailyProxyInputError(f"{owner}의 lineage record ID가 필요하다")
        for value in values:
            if not value.strip():
                raise KrxDailyProxyInputError(f"{owner}에 빈 lineage record ID가 있다")
            if value not in self._seen:
                self._seen.add(value)
                self._values.append(value)
