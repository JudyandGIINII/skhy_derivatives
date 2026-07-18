"""P1-06 검증: 비용 항목 분리·2배 스트레스 (PRD 10.4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.engine.cost_model import CostModelParams, estimate_transaction_cost

_PARAMS = CostModelParams(
    commission_rate=Decimal("0.00015"),
    tax_rate=Decimal("0.0018"),
    market_impact_coefficient=Decimal("0.1"),
)


def test_commission_scales_with_notional() -> None:
    cost = estimate_transaction_cost(
        bid_price=Decimal("100"),
        ask_price=Decimal("100"),
        order_quantity=Decimal("1000"),
        quote_depth=Decimal("10000"),
        params=_PARAMS,
        is_sell=False,
    )
    expected_notional = Decimal("100") * Decimal("1000")
    assert cost.commission == expected_notional * _PARAMS.commission_rate


def test_tax_only_applies_on_sell() -> None:
    buy_cost = estimate_transaction_cost(
        Decimal("100"), Decimal("100"), Decimal("1000"), Decimal("10000"), _PARAMS, is_sell=False
    )
    sell_cost = estimate_transaction_cost(
        Decimal("100"), Decimal("100"), Decimal("1000"), Decimal("10000"), _PARAMS, is_sell=True
    )
    assert buy_cost.tax == Decimal("0")
    assert sell_cost.tax > Decimal("0")


def test_spread_cost_is_half_spread_times_quantity() -> None:
    cost = estimate_transaction_cost(
        bid_price=Decimal("99"),
        ask_price=Decimal("101"),
        order_quantity=Decimal("100"),
        quote_depth=Decimal("10000"),
        params=_PARAMS,
        is_sell=False,
    )
    assert cost.spread_cost == Decimal("1") * Decimal("100")  # half_spread=1


def test_market_impact_increases_with_participation() -> None:
    low_participation = estimate_transaction_cost(
        Decimal("100"), Decimal("100"), Decimal("10"), Decimal("100000"), _PARAMS, is_sell=False
    )
    high_participation = estimate_transaction_cost(
        Decimal("100"), Decimal("100"), Decimal("50000"), Decimal("100000"), _PARAMS, is_sell=False
    )
    assert high_participation.market_impact_cost > low_participation.market_impact_cost


def test_total_is_sum_of_components() -> None:
    cost = estimate_transaction_cost(
        Decimal("99"), Decimal("101"), Decimal("100"), Decimal("10000"), _PARAMS, is_sell=True
    )
    assert cost.total == cost.commission + cost.tax + cost.spread_cost + cost.slippage_cost + cost.market_impact_cost


def test_stressed_doubles_every_component() -> None:
    cost = estimate_transaction_cost(
        Decimal("99"), Decimal("101"), Decimal("100"), Decimal("10000"), _PARAMS, is_sell=True
    )
    stressed = cost.stressed(Decimal("2"))

    assert stressed.commission == cost.commission * 2
    assert stressed.tax == cost.tax * 2
    assert stressed.spread_cost == cost.spread_cost * 2
    assert stressed.market_impact_cost == cost.market_impact_cost * 2
    assert stressed.total == cost.total * 2


def test_negative_quantity_raises() -> None:
    with pytest.raises(ValueError, match="order_quantity"):
        estimate_transaction_cost(
            Decimal("100"), Decimal("100"), Decimal("-1"), Decimal("1000"), _PARAMS, is_sell=False
        )


def test_crossed_quote_raises() -> None:
    with pytest.raises(ValueError, match="bid_price"):
        estimate_transaction_cost(
            Decimal("101"), Decimal("100"), Decimal("10"), Decimal("1000"), _PARAMS, is_sell=False
        )


def test_zero_quote_depth_treats_participation_as_full() -> None:
    cost = estimate_transaction_cost(
        Decimal("100"), Decimal("100"), Decimal("10"), Decimal("0"), _PARAMS, is_sell=False
    )
    assert cost.market_impact_cost > Decimal("0")
