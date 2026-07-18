"""FR-09: ADV 정규화 종가 압력 지표 (PRD 9.1).

estimated_close_pressure(t)
  = sum(kappa_i,regime * theoretical_delta_exposure_i(t) + observable_flow_adjustment_i(t))
    / underlying_20d_adv_notional

`observable_flow_adjustment`가 결측인 상품이 하나라도 있으면 0으로 몰래
대체하지 않고 `model_version="reduced"`로 낮추며 어떤 상품이 결측인지 남긴다
(G-03 미해소 시 기본 동작).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ORIGINAL_H1_DATA_RESOLUTION = "intraday"
ORIGINAL_H1_PROMOTION_SCOPE = "h1-original"


@dataclass(frozen=True)
class FundContribution:
    fund_id: str
    theoretical_delta_exposure: Decimal
    kappa: Decimal
    observable_flow_adjustment: Decimal | None  # None = 결측(G-03)


@dataclass(frozen=True)
class ClosePressureResult:
    value: Decimal
    model_version: str  # "full" | "reduced" | 별도 축소모델 ID
    missing_flow_fund_ids: tuple[str, ...]
    data_resolution: str = ORIGINAL_H1_DATA_RESOLUTION
    promotion_scope: str = ORIGINAL_H1_PROMOTION_SCOPE
    promotion_eligible: bool = True


def estimated_close_pressure(
    contributions: list[FundContribution], underlying_20d_adv_notional: Decimal
) -> ClosePressureResult:
    if underlying_20d_adv_notional == 0:
        raise ValueError("underlying_20d_adv_notional은 0일 수 없다")

    missing = tuple(c.fund_id for c in contributions if c.observable_flow_adjustment is None)
    total = Decimal("0")
    for c in contributions:
        flow = c.observable_flow_adjustment if c.observable_flow_adjustment is not None else Decimal("0")
        total += c.kappa * c.theoretical_delta_exposure + flow

    return ClosePressureResult(
        value=total / underlying_20d_adv_notional,
        model_version="reduced" if missing else "full",
        missing_flow_fund_ids=missing,
    )
