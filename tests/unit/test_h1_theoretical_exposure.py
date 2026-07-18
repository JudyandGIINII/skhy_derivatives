"""P1-03 검증: beta=2/-1/-2 이론 노출 부호 테스트 (PRD 14.3)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.features.h1_close_pressure.theoretical_exposure import (
    rebalancing_coefficient,
    theoretical_delta_exposure,
)


@pytest.mark.parametrize(
    ("beta", "expected_coefficient"),
    [
        (Decimal("2"), Decimal("2")),  # 2x 레버리지: 2*(2-1)=2
        (Decimal("-1"), Decimal("2")),  # -1x 인버스: -1*(-1-1)=2
        (Decimal("-2"), Decimal("6")),  # -2x 인버스: -2*(-2-1)=6
    ],
)
def test_rebalancing_coefficient_matches_prd_formula(beta: Decimal, expected_coefficient: Decimal) -> None:
    assert rebalancing_coefficient(beta) == expected_coefficient


@pytest.mark.parametrize("beta", [Decimal("2"), Decimal("-1"), Decimal("-2")])
def test_positive_underlying_return_produces_positive_delta_exposure(beta: Decimal) -> None:
    delta = theoretical_delta_exposure(beta, prior_nav=Decimal("1000000"), underlying_return=Decimal("0.02"))
    assert delta > 0


@pytest.mark.parametrize("beta", [Decimal("2"), Decimal("-1"), Decimal("-2")])
def test_negative_underlying_return_produces_negative_delta_exposure(beta: Decimal) -> None:
    delta = theoretical_delta_exposure(beta, prior_nav=Decimal("1000000"), underlying_return=Decimal("-0.02"))
    assert delta < 0


def test_delta_exposure_scales_linearly_with_prior_nav() -> None:
    small = theoretical_delta_exposure(Decimal("2"), Decimal("1000"), Decimal("0.01"))
    large = theoretical_delta_exposure(Decimal("2"), Decimal("10000"), Decimal("0.01"))
    assert large == small * 10


def test_delta_exposure_exact_value_for_2x_leverage() -> None:
    delta = theoretical_delta_exposure(Decimal("2"), Decimal("1000000"), Decimal("0.01"))
    assert delta == Decimal("2") * Decimal("1000000") * Decimal("0.01")  # coefficient=2
