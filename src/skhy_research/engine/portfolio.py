"""결정론적 포트폴리오 원장 (P1-05). fill을 적용한 순서에만 의존한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class PortfolioLedger:
    cash_by_currency: dict[str, Decimal] = field(default_factory=dict)
    positions: dict[str, Decimal] = field(default_factory=dict)  # instrument_id -> qty(음수=숏)
    avg_cost: dict[str, Decimal] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0")

    def apply_fill(
        self, instrument_id: str, side: str, quantity: Decimal, price: Decimal, currency: str
    ) -> None:
        signed_qty = quantity if side == "BUY" else -quantity
        prior_qty = self.positions.get(instrument_id, Decimal("0"))
        prior_avg = self.avg_cost.get(instrument_id, Decimal("0"))

        notional = quantity * price
        self.cash_by_currency[currency] = self.cash_by_currency.get(
            currency, Decimal("0")
        ) - (notional if side == "BUY" else -notional)

        new_qty = prior_qty + signed_qty
        same_direction = prior_qty == 0 or (prior_qty > 0) == (signed_qty > 0)

        if same_direction:
            total_cost = prior_avg * abs(prior_qty) + price * abs(signed_qty)
            self.avg_cost[instrument_id] = total_cost / abs(new_qty) if new_qty != 0 else Decimal("0")
        else:
            closed_qty = min(abs(signed_qty), abs(prior_qty))
            direction = Decimal("1") if prior_qty > 0 else Decimal("-1")
            self.realized_pnl += direction * (price - prior_avg) * closed_qty
            if new_qty != 0 and abs(new_qty) > abs(prior_qty):
                # 포지션 방향이 반전되어 기존 수량을 넘어선 부분은 새 평단으로 시작한다.
                self.avg_cost[instrument_id] = price
            elif new_qty == 0:
                self.avg_cost[instrument_id] = Decimal("0")
            # 기존 방향 그대로 일부만 청산된 경우 평단은 변하지 않는다.

        self.positions[instrument_id] = new_qty

    def unrealized_pnl(self, mark_prices: dict[str, Decimal]) -> Decimal:
        total = Decimal("0")
        for instrument_id, qty in self.positions.items():
            if qty == 0:
                continue
            mark = mark_prices.get(instrument_id)
            if mark is None:
                continue
            avg = self.avg_cost.get(instrument_id, Decimal("0"))
            total += (mark - avg) * qty
        return total
