"""FR-09: ADV 정규화 종가 압력 지표 (PRD 9.1).

estimated_close_pressure(t)
  = sum(kappa_i,regime * theoretical_delta_exposure_i(t) + observable_flow_adjustment_i(t))
    / underlying_20d_adv_notional

`observable_flow_adjustment`가 결측인 상품이 하나라도 있으면 full 합계의 0으로 몰래
대체하지 않고 별도 theoretical-only 축소모델로 낮추며 어떤 상품·필드가 결측인지 남긴다
(G-03 미해소 시 기본 동작).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ORIGINAL_H1_DATA_RESOLUTION = "intraday"
ORIGINAL_H1_LIVE_DATA_RESOLUTION = "intraday-live-1m-snapshot"
ORIGINAL_H1_PROMOTION_SCOPE = "h1-original"
H1_CLOSE_PRESSURE_FULL_MODEL_VERSION = "h1_close_pressure_full_v1"
H1_CLOSE_PRESSURE_REDUCED_MODEL_VERSION = "h1_close_pressure_missing_g03_v1"


@dataclass(frozen=True)
class MissingFlowInput:
    fund_id: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class FundContribution:
    fund_id: str
    theoretical_delta_exposure: Decimal
    kappa: Decimal
    observable_flow_adjustment: Decimal | None  # None = 결측(G-03)
    missing_flow_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClosePressureResult:
    value: Decimal
    model_version: str  # full/reduced를 구분하는 고정 모델 ID
    missing_flow_fund_ids: tuple[str, ...]
    missing_flow_inputs: tuple[MissingFlowInput, ...] = ()
    data_resolution: str = ORIGINAL_H1_DATA_RESOLUTION
    promotion_scope: str = ORIGINAL_H1_PROMOTION_SCOPE
    promotion_eligible: bool = True


def estimated_close_pressure(
    contributions: list[FundContribution], underlying_20d_adv_notional: Decimal
) -> ClosePressureResult:
    if underlying_20d_adv_notional <= 0:
        raise ValueError("underlying_20d_adv_notional은 0보다 커야 한다")

    missing_inputs = tuple(
        MissingFlowInput(
            c.fund_id,
            c.missing_flow_fields or ("observable_flow_adjustment",),
        )
        for c in contributions
        if c.observable_flow_adjustment is None
    )
    missing = tuple(item.fund_id for item in missing_inputs)
    total = Decimal("0")
    for c in contributions:
        total += c.kappa * c.theoretical_delta_exposure
        if c.observable_flow_adjustment is not None:
            total += c.observable_flow_adjustment

    return ClosePressureResult(
        value=total / underlying_20d_adv_notional,
        model_version=(
            H1_CLOSE_PRESSURE_REDUCED_MODEL_VERSION
            if missing
            else H1_CLOSE_PRESSURE_FULL_MODEL_VERSION
        ),
        missing_flow_fund_ids=missing,
        missing_flow_inputs=missing_inputs,
        promotion_eligible=not missing,
    )
