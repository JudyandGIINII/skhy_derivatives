"""P0-09 검증: 정규화 실패를 조용히 삼키지 않고 NormalizationError로 명시한다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.data.normalization.errors import NormalizationError
from skhy_research.data.normalization.market_quote_normalizer import normalize_market_quote

_NOW = 1_800_000_000_000_000_000


def _valid_quote_row() -> dict:
    return {
        "source": "kis",
        "venue": "KRX",
        "symbol": "000660",
        "event_time_utc": _NOW,
        "received_time_utc": _NOW + 1000,
        "currency": "KRW",
        "session": "REGULAR",
        "is_delayed": False,
        "adjustment_status": "RAW",
        "instrument_id": "SKHY_000660_KRX_COMMON",
        "bid_price": Decimal("202900"),
        "ask_price": Decimal("203000"),
        "bid_size": Decimal("120"),
        "ask_size": Decimal("95"),
    }


def test_normalize_valid_payload_succeeds() -> None:
    quote = normalize_market_quote("kis", "quotes", _valid_quote_row())
    assert quote.instrument_id == "SKHY_000660_KRX_COMMON"


def test_normalize_missing_field_raises_normalization_error_not_silent_default() -> None:
    row = _valid_quote_row()
    del row["bid_price"]

    with pytest.raises(NormalizationError) as exc_info:
        normalize_market_quote("kis", "quotes", row, raw_record_id="raw-123")

    assert exc_info.value.source == "kis"
    assert exc_info.value.raw_record_id == "raw-123"


def test_normalize_negative_price_raises_not_clamped_to_zero() -> None:
    row = _valid_quote_row()
    row["bid_price"] = Decimal("-1")

    with pytest.raises(NormalizationError):
        normalize_market_quote("kis", "quotes", row)
