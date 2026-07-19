"""P0-04 검증: RecordEnvelope 계열 타입의 null 사유·시간·범위 불변조건."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    QualityFlag,
    Session,
    Venue,
)
from skhy_research.domain.market import Bar, BarConstructionMethod, FXQuote, MarketQuote, Trade

_NOW = 1_800_000_000_000_000_000  # 임의의 UTC epoch ns


def _envelope_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        source="KIS",
        venue=Venue.KRX,
        symbol="000660",
        event_time_utc=_NOW,
        received_time_utc=_NOW + 1_000_000,
        currency=Currency.KRW,
        session=Session.REGULAR,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.RAW,
    )
    base.update(overrides)
    return base


def test_record_envelope_allows_null_currency_with_reason() -> None:
    quote = MarketQuote(
        **_envelope_kwargs(currency=None, currency_na_reason="REFERENCE_ONLY_NO_PRICE"),
        instrument_id="SKHY_000660",
        bid_price=Decimal("100"),
        ask_price=Decimal("101"),
        bid_size=Decimal("10"),
        ask_size=Decimal("10"),
    )
    assert quote.currency is None
    assert quote.currency_na_reason == "REFERENCE_ONLY_NO_PRICE"


def test_record_envelope_rejects_null_currency_without_reason() -> None:
    with pytest.raises(ValidationError, match="currency_na_reason"):
        MarketQuote(
            **_envelope_kwargs(currency=None),
            instrument_id="SKHY_000660",
            bid_price=Decimal("100"),
            ask_price=Decimal("101"),
            bid_size=Decimal("10"),
            ask_size=Decimal("10"),
        )


def test_record_envelope_rejects_received_before_event() -> None:
    with pytest.raises(ValidationError, match="received_time_utc"):
        MarketQuote(
            **_envelope_kwargs(event_time_utc=_NOW, received_time_utc=_NOW - 1),
            instrument_id="SKHY_000660",
            bid_price=Decimal("100"),
            ask_price=Decimal("101"),
            bid_size=Decimal("10"),
            ask_size=Decimal("10"),
        )


def test_market_quote_allows_crossed_quote_but_flags_negative_size() -> None:
    # crossed quote(bid>ask)는 구조적으로는 유효하다 — P0-09 품질 레이어가 탐지한다.
    crossed = MarketQuote(
        **_envelope_kwargs(quality_flag=[QualityFlag.SOURCE_DIVERGENCE]),
        instrument_id="SKHY_000660",
        bid_price=Decimal("101"),
        ask_price=Decimal("100"),
        bid_size=Decimal("10"),
        ask_size=Decimal("10"),
    )
    assert crossed.bid_price > crossed.ask_price
    assert QualityFlag.SOURCE_DIVERGENCE in crossed.quality_flag

    with pytest.raises(ValidationError):
        MarketQuote(
            **_envelope_kwargs(),
            instrument_id="SKHY_000660",
            bid_price=Decimal("-1"),
            ask_price=Decimal("100"),
            bid_size=Decimal("10"),
            ask_size=Decimal("10"),
        )


def test_trade_side_is_optional() -> None:
    trade = Trade(
        **_envelope_kwargs(),
        instrument_id="SKHY_000660",
        price=Decimal("100"),
        quantity=Decimal("5"),
    )
    assert trade.side is None


def test_bar_rejects_open_outside_high_low_range() -> None:
    with pytest.raises(ValidationError, match="open"):
        Bar(
            **_envelope_kwargs(),
            instrument_id="SKHY_000660",
            period="1d",
            open=Decimal("200"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("100"),
            volume=Decimal("1000"),
            is_adjusted=False,
            construction=BarConstructionMethod(
                method="VENDOR_PROVIDED", source_segment="KRX:2026-01-01..2026-03-31"
            ),
            bar_close_time_utc=_NOW,
        )


def test_bar_accepts_valid_ohlc() -> None:
    bar = Bar(
        **_envelope_kwargs(),
        instrument_id="SKHY_000660",
        period="1m",
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("102"),
        volume=Decimal("1000"),
        is_adjusted=True,
        construction=BarConstructionMethod(
            method="AGGREGATED_FROM_TICKS", source_segment="KIS:2026-07-01..2026-07-18"
        ),
        bar_close_time_utc=_NOW,
    )
    assert bar.close == Decimal("102")


def test_fxquote_rejects_non_usdkrw_pair() -> None:
    with pytest.raises(ValidationError, match="USD/KRW"):
        FXQuote(
            **_envelope_kwargs(venue=Venue.REFERENCE, session=Session.REFERENCE, symbol="USD/HKD"),
            pair="USD/HKD",
            bid=Decimal("7.8"),
            ask=Decimal("7.81"),
            rate_kind="EXECUTION",
        )


def test_fxquote_accepts_default_usdkrw_pair() -> None:
    fx = FXQuote(
        **_envelope_kwargs(venue=Venue.REFERENCE, session=Session.REFERENCE, symbol="USD/KRW"),
        bid=Decimal("1380.5"),
        ask=Decimal("1381.0"),
        rate_kind="DAILY_REFERENCE",
    )
    assert fx.pair == "USD/KRW"
