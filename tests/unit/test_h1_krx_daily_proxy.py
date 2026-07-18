"""무료 KRX 일별정보 기반 H1 daily-proxy feature 계약."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from skhy_research.application.h1_krx_daily_proxy import (
    KRX_DAILY_PROXY_DATA_RESOLUTION,
    KRX_DAILY_PROXY_MODEL_VERSION,
    KRX_DAILY_PROXY_PROMOTION_SCOPE,
    KrxDailyProxyFundInput,
    KrxDailyProxyInputError,
    KrxDailyProxyMarketInput,
    build_krx_daily_proxy_feature,
    calculate_20d_adv_notional,
    calculate_daily_return_proxy,
)

_BASIS_DATE = date(2026, 7, 16)
_SIGNAL_DATE = date(2026, 7, 17)
_RECEIVED_AT = 1_799_999_999_000_000_000
_AS_OF = 1_800_000_000_000_000_000


def _market() -> KrxDailyProxyMarketInput:
    return KrxDailyProxyMarketInput(
        basis_date=_BASIS_DATE,
        previous_close=Decimal("100"),
        close=Decimal("110"),
        turnover_notional_20d=(Decimal("1000000"),) * 20,
        received_at_utc=_RECEIVED_AT,
        input_record_ids=("raw-market",),
    )


def _funds() -> list[KrxDailyProxyFundInput]:
    return [
        KrxDailyProxyFundInput(
            fund_id="FUND_LONG_2X",
            beta=Decimal("2"),
            nav_or_iv=Decimal("100"),
            listed_shares=Decimal("1000"),
            kappa=Decimal("0.5"),
            basis_date=_BASIS_DATE,
            received_at_utc=_RECEIVED_AT,
            input_record_ids=("raw-fund-long",),
        ),
        KrxDailyProxyFundInput(
            fund_id="FUND_INVERSE_2X",
            beta=Decimal("-2"),
            nav_or_iv=Decimal("50"),
            listed_shares=Decimal("2000"),
            kappa=Decimal("0.25"),
            basis_date=_BASIS_DATE,
            received_at_utc=_RECEIVED_AT,
            input_record_ids=("raw-fund-inverse",),
        ),
    ]


def test_builds_listed_notional_and_reuses_close_pressure_contract() -> None:
    result = build_krx_daily_proxy_feature(
        _funds(), _market(), signal_date=_SIGNAL_DATE, as_of_time_utc=_AS_OF
    )

    # daily return=10%; +2x coefficient=2, -2x coefficient=6
    assert result.underlying_daily_return_proxy == Decimal("0.1")
    assert result.underlying_20d_adv_notional == Decimal("1000000")
    assert [item.listed_notional_proxy for item in result.fund_features] == [
        Decimal("100000"),
        Decimal("100000"),
    ]
    assert [item.theoretical_delta_exposure for item in result.fund_features] == [
        Decimal("20000.0"),
        Decimal("60000.0"),
    ]
    assert result.close_pressure.value == Decimal("0.025")
    assert result.close_pressure.missing_flow_fund_ids == (
        "FUND_LONG_2X",
        "FUND_INVERSE_2X",
    )
    assert result.input_record_ids == (
        "raw-market",
        "raw-fund-long",
        "raw-fund-inverse",
    )


def test_every_proxy_output_is_tagged_and_not_promotion_eligible() -> None:
    result = build_krx_daily_proxy_feature(
        _funds(), _market(), signal_date=_SIGNAL_DATE, as_of_time_utc=_AS_OF
    )

    tagged_outputs = [result, result.close_pressure, *result.fund_features]
    for item in tagged_outputs:
        assert item.model_version == KRX_DAILY_PROXY_MODEL_VERSION
        assert item.data_resolution == KRX_DAILY_PROXY_DATA_RESOLUTION
        assert item.promotion_scope == KRX_DAILY_PROXY_PROMOTION_SCOPE
        assert item.promotion_eligible is False


def test_same_day_basis_date_is_rejected() -> None:
    same_day_market = replace(_market(), basis_date=_SIGNAL_DATE)
    with pytest.raises(KrxDailyProxyInputError, match="signal_date"):
        build_krx_daily_proxy_feature(
            _funds(), same_day_market, signal_date=_SIGNAL_DATE, as_of_time_utc=_AS_OF
        )


def test_data_received_at_or_after_as_of_is_rejected() -> None:
    late_fund = replace(_funds()[0], received_at_utc=_AS_OF)
    with pytest.raises(KrxDailyProxyInputError, match="as_of"):
        build_krx_daily_proxy_feature(
            [late_fund], _market(), signal_date=_SIGNAL_DATE, as_of_time_utc=_AS_OF
        )


def test_missing_lineage_is_rejected() -> None:
    no_lineage = replace(_funds()[0], input_record_ids=())
    with pytest.raises(KrxDailyProxyInputError, match="lineage"):
        build_krx_daily_proxy_feature(
            [no_lineage], _market(), signal_date=_SIGNAL_DATE, as_of_time_utc=_AS_OF
        )


def test_non_positive_nav_or_listed_shares_is_rejected() -> None:
    with pytest.raises(KrxDailyProxyInputError, match="NAV/IV"):
        build_krx_daily_proxy_feature(
            [replace(_funds()[0], nav_or_iv=Decimal("0"))],
            _market(),
            signal_date=_SIGNAL_DATE,
            as_of_time_utc=_AS_OF,
        )
    with pytest.raises(KrxDailyProxyInputError, match="listed_shares"):
        build_krx_daily_proxy_feature(
            [replace(_funds()[0], listed_shares=Decimal("0"))],
            _market(),
            signal_date=_SIGNAL_DATE,
            as_of_time_utc=_AS_OF,
        )


def test_daily_return_and_adv_helpers_reject_invalid_windows() -> None:
    assert calculate_daily_return_proxy(Decimal("100"), Decimal("95")) == Decimal("-0.05")
    with pytest.raises(KrxDailyProxyInputError, match="종가"):
        calculate_daily_return_proxy(Decimal("0"), Decimal("95"))
    with pytest.raises(KrxDailyProxyInputError, match="정확히 20개"):
        calculate_20d_adv_notional((Decimal("1"),) * 19)
