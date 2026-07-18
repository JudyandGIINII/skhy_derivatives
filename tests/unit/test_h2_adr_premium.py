"""Unit tests for Change A: H2 ADR-premium feature computation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.domain.enums import AdjustmentStatus, Currency, QualityFlag, Session, Venue
from skhy_research.domain.market import FXQuote, MarketQuote
from skhy_research.features.h2_adr_premium.premium import compute_adr_premium

_NOW = 1_800_000_000_000_000_000


def _make_market_quote(
    instrument_id: str,
    bid_price: str,
    ask_price: str,
    session: Session = Session.REGULAR,
    quality_flags: list[QualityFlag] | None = None,
) -> MarketQuote:
    return MarketQuote(
        source="KIS",
        venue=Venue.NASDAQ if "SKHY" in instrument_id else Venue.KRX,
        symbol="SKHY" if "SKHY" in instrument_id else "000660",
        event_time_utc=_NOW,
        received_time_utc=_NOW + 1_000_000,
        currency=Currency.USD if "SKHY" in instrument_id else Currency.KRW,
        session=session,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        quality_flag=quality_flags or [],
        instrument_id=instrument_id,
        bid_price=Decimal(bid_price),
        ask_price=Decimal(ask_price),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
    )


def _make_fx_quote(
    bid: str,
    ask: str,
    pair: str = "USD/KRW",
    session: Session = Session.REGULAR,
    quality_flags: list[QualityFlag] | None = None,
) -> FXQuote:
    # We bypass FXQuote Pydantic validation for non-USD/KRW in python if needed,
    # but the pydantic model enforces USD/KRW. So we will catch validation or value errors.
    return FXQuote(
        source="TOSS",
        venue=Venue.REFERENCE,
        symbol=pair,
        event_time_utc=_NOW,
        received_time_utc=_NOW + 1_000_000,
        currency=Currency.KRW,
        session=session,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        quality_flag=quality_flags or [],
        pair=pair,
        bid=Decimal(bid),
        ask=Decimal(ask),
        rate_kind="DAILY_REFERENCE",
    )


def test_adr_fair_usd_and_premium_10_to_1_ratio() -> None:
    # 10:1 ADR ratio test
    # kr_common_krw mid = (149_000 + 151_000) / 2 = 150_000 KRW
    # usdkrw mid = (1495 + 1505) / 2 = 1500 KRW/USD
    # adr_fair_usd = 150_000 / 1500 / 10 = 10 USD
    # skhy_adr mid = (11.4 + 11.6) / 2 = 11.5 USD
    # premium = 11.5 / 10 - 1 = 0.15 (15%)
    kr_common = _make_market_quote("SKHY_000660_KRX_COMMON", "149000", "151000")
    skhy_adr = _make_market_quote("SKHY_NASDAQ_ADR", "11.4", "11.6")
    usdkrw = _make_fx_quote("1495", "1505")

    result = compute_adr_premium(
        kr_common=kr_common,
        skhy_adr=skhy_adr,
        usdkrw=usdkrw,
        estimated_entry_cost_rate=Decimal("0.01"),
        estimated_exit_cost_rate=Decimal("0.02"),
    )

    assert result.adr_fair_usd == Decimal("10")
    assert result.premium == Decimal("0.15")
    assert not result.stale_reference


def test_bid_ask_legs_premium_calculation() -> None:
    # bid/ask legs calculation check
    # skhy_bid_usd = 11, skhy_ask_usd = 12
    # usdkrw_bid = 1500, usdkrw_ask = 1510
    # kr_ask_krw = 150000, kr_bid_krw = 140000
    # entry cost rate = 0.01, exit cost rate = 0.02
    # executable_entry_premium = (11 * 10 * 1500 - 150000) / 150000 - 0.01 = 0.10 - 0.01 = 0.09
    # executable_exit_premium = (12 * 10 * 1510 - 140000) / 140000 + 0.02
    #                          = 41200 / 140000 + 0.02 = 0.2942857142857142857142857143 + 0.02 = 0.3142857142857142857142857143
    kr_common = _make_market_quote("SKHY_000660_KRX_COMMON", "140000", "150000")
    skhy_adr = _make_market_quote("SKHY_NASDAQ_ADR", "11", "12")
    usdkrw = _make_fx_quote("1500", "1510")

    result = compute_adr_premium(
        kr_common=kr_common,
        skhy_adr=skhy_adr,
        usdkrw=usdkrw,
        estimated_entry_cost_rate=Decimal("0.01"),
        estimated_exit_cost_rate=Decimal("0.02"),
    )

    assert result.executable_entry_premium == Decimal("0.09")
    expected_exit = (Decimal("12") * Decimal("10") * Decimal("1510") - Decimal("140000")) / Decimal(
        "140000"
    ) + Decimal("0.02")
    assert result.executable_exit_premium == expected_exit


def test_usd_krw_direction_enforcement() -> None:
    # Attempting to use a non USD/KRW pair should trigger value error or validation error.
    # Note that FXQuote validation itself enforces pair == "USD/KRW".
    # If we bypass or use a mock with pair not USD/KRW:
    kr_common = _make_market_quote("SKHY_000660_KRX_COMMON", "149000", "151000")
    skhy_adr = _make_market_quote("SKHY_NASDAQ_ADR", "11.4", "11.6")

    class FakeFXQuote:
        pair = "USD/HKD"
        bid = Decimal("7.8")
        ask = Decimal("7.8")
        session = Session.REGULAR
        quality_flag = []

    with pytest.raises(ValueError, match="FXQuote pair must be USD/KRW"):
        compute_adr_premium(
            kr_common=kr_common,
            skhy_adr=skhy_adr,
            usdkrw=FakeFXQuote(),  # type: ignore
            estimated_entry_cost_rate=Decimal("0.01"),
            estimated_exit_cost_rate=Decimal("0.01"),
        )


def test_stale_reference_forcing() -> None:
    kr_common = _make_market_quote("SKHY_000660_KRX_COMMON", "149000", "151000")
    skhy_adr = _make_market_quote("SKHY_NASDAQ_ADR", "11.4", "11.6")
    usdkrw = _make_fx_quote("1495", "1505")

    # 1. force_stale=True overrides everything
    res_force = compute_adr_premium(
        kr_common=kr_common,
        skhy_adr=skhy_adr,
        usdkrw=usdkrw,
        estimated_entry_cost_rate=Decimal("0.01"),
        estimated_exit_cost_rate=Decimal("0.01"),
        force_stale=True,
    )
    assert res_force.stale_reference is True

    # 2. Market closed session in KR
    kr_closed = _make_market_quote("SKHY_000660_KRX_COMMON", "149000", "151000", session=Session.CLOSED)
    res_kr_closed = compute_adr_premium(
        kr_common=kr_closed,
        skhy_adr=skhy_adr,
        usdkrw=usdkrw,
        estimated_entry_cost_rate=Decimal("0.01"),
        estimated_exit_cost_rate=Decimal("0.01"),
    )
    assert res_kr_closed.stale_reference is True

    # 3. Market closed session in Nasdaq ADR
    adr_closed = _make_market_quote("SKHY_NASDAQ_ADR", "11.4", "11.6", session=Session.CLOSED)
    res_adr_closed = compute_adr_premium(
        kr_common=kr_common,
        skhy_adr=adr_closed,
        usdkrw=usdkrw,
        estimated_entry_cost_rate=Decimal("0.01"),
        estimated_exit_cost_rate=Decimal("0.01"),
    )
    assert res_adr_closed.stale_reference is True

    # 4. Quality flag STALE in FX
    fx_stale = _make_fx_quote("1495", "1505", quality_flags=[QualityFlag.STALE])
    res_fx_stale = compute_adr_premium(
        kr_common=kr_common,
        skhy_adr=skhy_adr,
        usdkrw=fx_stale,
        estimated_entry_cost_rate=Decimal("0.01"),
        estimated_exit_cost_rate=Decimal("0.01"),
    )
    assert res_fx_stale.stale_reference is True

    # 5. Quality flag MARKET_CLOSED in US ADR
    adr_mkt_closed = _make_market_quote(
        "SKHY_NASDAQ_ADR", "11.4", "11.6", quality_flags=[QualityFlag.MARKET_CLOSED]
    )
    res_adr_mkt_closed = compute_adr_premium(
        kr_common=kr_common,
        skhy_adr=adr_mkt_closed,
        usdkrw=usdkrw,
        estimated_entry_cost_rate=Decimal("0.01"),
        estimated_exit_cost_rate=Decimal("0.01"),
    )
    assert res_adr_mkt_closed.stale_reference is True
