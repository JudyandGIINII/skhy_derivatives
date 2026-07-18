"""P1-03 검증: 결측 flow가 있으면 0으로 몰래 대체하지 않고 축소모델로 낮춘다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.features.h1_close_pressure.close_pressure import (
    H1_CLOSE_PRESSURE_FULL_MODEL_VERSION,
    H1_CLOSE_PRESSURE_REDUCED_MODEL_VERSION,
    ClosePressureResult,
    FundContribution,
    estimated_close_pressure,
)


def test_full_model_when_all_flows_present() -> None:
    contributions = [
        FundContribution("FUND_A", Decimal("10000"), Decimal("0.3"), Decimal("500")),
        FundContribution("FUND_B", Decimal("5000"), Decimal("0.2"), Decimal("100")),
    ]
    result = estimated_close_pressure(contributions, underlying_20d_adv_notional=Decimal("1000000"))

    expected_numerator = (Decimal("0.3") * 10000 + 500) + (Decimal("0.2") * 5000 + 100)
    assert result.value == expected_numerator / Decimal("1000000")
    assert result.model_version == H1_CLOSE_PRESSURE_FULL_MODEL_VERSION
    assert result.missing_flow_fund_ids == ()
    assert result.promotion_eligible is True


def test_missing_flow_downgrades_to_reduced_model_and_names_fund() -> None:
    contributions = [
        FundContribution("FUND_A", Decimal("10000"), Decimal("0.3"), Decimal("500")),
        FundContribution(
            "FUND_B",
            Decimal("5000"),
            Decimal("0.2"),
            None,
            ("program_net_buy",),
        ),
    ]
    result = estimated_close_pressure(contributions, underlying_20d_adv_notional=Decimal("1000000"))

    assert result.model_version == H1_CLOSE_PRESSURE_REDUCED_MODEL_VERSION
    assert result.missing_flow_fund_ids == ("FUND_B",)
    assert result.missing_flow_inputs[0].fields == ("program_net_buy",)
    assert result.promotion_eligible is False
    # 축소모델 값은 theoretical-only이며 full 관측값으로 위장되지 않는다.
    expected_numerator = (Decimal("0.3") * 10000 + 500) + (Decimal("0.2") * 5000 + 0)
    assert result.value == expected_numerator / Decimal("1000000")


def test_zero_adv_raises_instead_of_dividing_by_zero() -> None:
    with pytest.raises(ValueError, match="underlying_20d_adv_notional"):
        estimated_close_pressure([], underlying_20d_adv_notional=Decimal("0"))

    with pytest.raises(ValueError, match="underlying_20d_adv_notional"):
        estimated_close_pressure([], underlying_20d_adv_notional=Decimal("-1"))


def test_empty_contributions_yields_zero_full_model() -> None:
    result = estimated_close_pressure([], underlying_20d_adv_notional=Decimal("1000000"))
    assert result == ClosePressureResult(Decimal("0"), H1_CLOSE_PRESSURE_FULL_MODEL_VERSION, ())


def test_multiple_missing_funds_are_all_recorded() -> None:
    contributions = [
        FundContribution("FUND_A", Decimal("10000"), Decimal("0.3"), None),
        FundContribution("FUND_B", Decimal("5000"), Decimal("0.2"), None),
    ]
    result = estimated_close_pressure(contributions, underlying_20d_adv_notional=Decimal("1000000"))
    assert set(result.missing_flow_fund_ids) == {"FUND_A", "FUND_B"}
    assert result.model_version == H1_CLOSE_PRESSURE_REDUCED_MODEL_VERSION
