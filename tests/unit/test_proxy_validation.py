"""Unit tests for Change B: Proxy method validation harness."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.domain.enums import AdjustmentStatus, Currency, Session, Venue
from skhy_research.domain.market import FXQuote, MarketQuote
from skhy_research.experiments.method_validation_harness import (
    ProxyBacktestResult,
    ProxyMethodValidationHarness,
)

_NOW = 1_800_000_000_000_000_000


def _quote(instrument_id: str, bid: str, ask: str, time_ns: int = _NOW) -> MarketQuote:
    is_nasdaq = "NASDAQ" in instrument_id
    symbol = "SEC_ADR" if is_nasdaq else "005930"
    return MarketQuote(
        source="KIS",
        venue=Venue.NASDAQ if is_nasdaq else Venue.KRX,
        symbol=symbol,
        event_time_utc=time_ns,
        received_time_utc=time_ns + 1_000_000,
        currency=Currency.USD if is_nasdaq else Currency.KRW,
        session=Session.REGULAR,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id=instrument_id,
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
    )


def _fx(bid: str, ask: str, time_ns: int = _NOW) -> FXQuote:
    return FXQuote(
        source="TOSS",
        venue=Venue.REFERENCE,
        symbol="USD/KRW",
        event_time_utc=time_ns,
        received_time_utc=time_ns + 1_000_000,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        pair="USD/KRW",
        bid=Decimal(bid),
        ask=Decimal(ask),
        rate_kind="DAILY_REFERENCE",
    )


def test_proxy_harness_rejects_sk_hynix_symbol_on_init() -> None:
    # Reject 000660
    with pytest.raises(ValueError, match="cannot be initialized with SK Hynix symbols"):
        ProxyMethodValidationHarness("000660", Decimal("0.01"), Decimal("0.01"))

    # Reject SKHY
    with pytest.raises(ValueError, match="cannot be initialized with SK Hynix symbols"):
        ProxyMethodValidationHarness("SKHY_NASDAQ_ADR", Decimal("0.01"), Decimal("0.01"))


def test_proxy_harness_rejects_sk_hynix_quotes_on_run() -> None:
    harness = ProxyMethodValidationHarness("005930", Decimal("0.001"), Decimal("0.001"))

    skhy_quote = MarketQuote(
        source="KIS",
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=_NOW,
        received_time_utc=_NOW + 1_000_000,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
        instrument_id="SKHY_000660_KRX_COMMON",
        bid_price=Decimal("150000"),
        ask_price=Decimal("151000"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
    )

    sec_adr = _quote("SEC_NASDAQ_ADR", "10", "11")
    usdkrw = _fx("1400", "1410")

    with pytest.raises(ValueError, match="SK Hynix common stock found in input quotes"):
        harness.run_simulation([skhy_quote], [sec_adr], [usdkrw])


def test_proxy_result_prevents_sk_hynix_flag() -> None:
    with pytest.raises(ValueError, match="strictly method-validation-only"):
        ProxyBacktestResult(
            is_method_validation_only=True,
            target_asset_is_skhy=True,  # Contamination!
            harness_label="METHOD_VALIDATION_ONLY_PROXIES",
            trades_executed=0,
            total_pnl_usd=Decimal("0"),
            average_premium=Decimal("0"),
            has_warnings=True,
            warnings=("WARNING",),
        )


def test_proxy_harness_simulation_logic() -> None:
    # Setup quotes for Samsung Electronics proxy
    # We want to simulate:
    # 1. Entry: premium becomes wide
    # 2. Exit: premium compresses
    harness = ProxyMethodValidationHarness("005930", Decimal("0.001"), Decimal("0.001"))

    # Event 1: Flat/Normal state
    # Common mid = 70,000 KRW, FX mid = 1,400. Fair USD = 70,000 / 1,400 / 10 = 5 USD.
    # ADR bid = 5.0, ask = 5.1 (mid = 5.05). Premium = 5.05 / 5 - 1 = 0.01.
    c1 = _quote("SEC_005930_KRX_COMMON", "69500", "70500", _NOW)
    a1 = _quote("SEC_NASDAQ_ADR", "5.0", "5.1", _NOW)
    f1 = _fx("1395", "1405", _NOW)

    # Event 2: Wide premium (Entry trigger)
    # Common ask = 70,000. FX bid = 1400.
    # ADR bid = 6.0.
    # executable_entry_premium = (6.0 * 10 * 1400 - 70000) / 70000 - 0.001 = (84000 - 70000) / 70000 - 0.001 = 0.2 - 0.001 = 0.199 > 0.05.
    c2 = _quote("SEC_005930_KRX_COMMON", "69000", "70000", _NOW + 10_000_000)
    a2 = _quote("SEC_NASDAQ_ADR", "6.0", "6.1", _NOW + 10_000_000)
    f2 = _fx("1400", "1410", _NOW + 10_000_000)

    # Event 3: Compressed premium (Exit trigger)
    # Common bid = 80,000. FX ask = 1400.
    # ADR ask = 8.0.
    # executable_exit_premium = (8.0 * 10 * 1400 - 80000) / 80000 + 0.001 = (112000 - 80000) / 80000 + 0.001 = 0.401. Wait, that's not compressed!
    # Let's make it compressed by dropping ADR price: ADR ask = 5.6.
    # executable_exit_premium = (5.6 * 10 * 1400 - 80000) / 80000 + 0.001 = (78400 - 80000) / 80000 + 0.001 = -0.02 + 0.001 = -0.019 <= 0.01.
    c3 = _quote("SEC_005930_KRX_COMMON", "80000", "81000", _NOW + 20_000_000)
    a3 = _quote("SEC_NASDAQ_ADR", "5.5", "5.6", _NOW + 20_000_000)
    f3 = _fx("1390", "1400", _NOW + 20_000_000)

    res = harness.run_simulation(
        common_quotes=[c1, c2, c3],
        adr_quotes=[a1, a2, a3],
        fx_quotes=[f1, f2, f3],
        entry_threshold=Decimal("0.05"),
        exit_threshold=Decimal("0.01"),
    )

    assert res.is_method_validation_only is True
    assert res.target_asset_is_skhy is False
    assert res.trades_executed == 1
    assert res.total_pnl_usd > 0
    assert len(res.warnings) == 2
    assert "SK Hynix performance" in res.warnings[1]
