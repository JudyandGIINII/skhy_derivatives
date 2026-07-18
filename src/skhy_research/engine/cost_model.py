"""거래 비용 모델: 수수료·세금·스프레드·시장충격 (PRD 10.4, FR-12).

각 비용 항목을 분리해 기본 비용과 2배 스트레스 비용을 동시에 낼 수 있게 한다.
슬리피지는 이 최소판에서는 체결 시점에(`fill_model.try_fill_leg`) 실측되므로
여기서는 0으로 두고, 사전 추정 단계(Signal.expected_cost)에서는 스프레드·
수수료·세금·시장충격만 반영한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CostBreakdown:
    commission: Decimal
    tax: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    market_impact_cost: Decimal

    @property
    def total(self) -> Decimal:
        return (
            self.commission
            + self.tax
            + self.spread_cost
            + self.slippage_cost
            + self.market_impact_cost
        )

    def stressed(self, multiplier: Decimal) -> CostBreakdown:
        return CostBreakdown(
            commission=self.commission * multiplier,
            tax=self.tax * multiplier,
            spread_cost=self.spread_cost * multiplier,
            slippage_cost=self.slippage_cost * multiplier,
            market_impact_cost=self.market_impact_cost * multiplier,
        )


@dataclass(frozen=True)
class CostModelParams:
    commission_rate: Decimal  # 매매대금 대비 비율
    tax_rate: Decimal  # 매도 시 증권거래세 등, 매매대금 대비 비율
    market_impact_coefficient: Decimal  # sqrt(참여율) 시장충격 계수


def estimate_transaction_cost(
    bid_price: Decimal,
    ask_price: Decimal,
    order_quantity: Decimal,
    quote_depth: Decimal,
    params: CostModelParams,
    is_sell: bool,
) -> CostBreakdown:
    if order_quantity < 0:
        raise ValueError("order_quantity는 음수일 수 없다")
    if bid_price > ask_price:
        raise ValueError("bid_price는 ask_price를 초과할 수 없다")

    mid_price = (bid_price + ask_price) / 2
    notional = mid_price * order_quantity

    commission = notional * params.commission_rate
    tax = notional * params.tax_rate if is_sell else Decimal("0")

    half_spread = (ask_price - bid_price) / 2
    spread_cost = half_spread * order_quantity

    participation = order_quantity / quote_depth if quote_depth > 0 else Decimal("1")
    market_impact_cost = (
        params.market_impact_coefficient * participation.sqrt() * mid_price * order_quantity
    )

    return CostBreakdown(
        commission=commission,
        tax=tax,
        spread_cost=spread_cost,
        slippage_cost=Decimal("0"),
        market_impact_cost=market_impact_cost,
    )
