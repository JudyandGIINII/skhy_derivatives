"""H1 이론적 노출 변화 (PRD 9.1).

theoretical_delta_exposure_i(t) ≈ beta_i * (beta_i - 1) * prior_nav_i * underlying_return_i(t)

계수 `beta*(beta-1)`는 beta<0이거나 beta>1이면 항상 양수다(레버리지·인버스
모두 기초자산 수익률과 같은 방향의 압력을 만든다는 1차 근사). 0<beta<1(부분
레버리지)에서만 음수가 될 수 있다.
"""

from __future__ import annotations

from decimal import Decimal


def rebalancing_coefficient(beta: Decimal) -> Decimal:
    return beta * (beta - Decimal("1"))


def theoretical_delta_exposure(
    beta: Decimal, prior_nav: Decimal, underlying_return: Decimal
) -> Decimal:
    return rebalancing_coefficient(beta) * prior_nav * underlying_return
