"""KRX ETF/ETN fixture에서 단일종목 레버리지 universe를 분류·등록한다."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from skhy_research.application.fund_snapshot_collection import discover_leveraged_products
from skhy_research.application.instrument_master import InstrumentMaster
from skhy_research.application.leverage_universe_discovery import (
    discover_and_register_krx_leveraged_universe,
)
from skhy_research.domain.enums import AssetClass

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "krx"
_BASIS_DATE = date(2026, 7, 16)


class _FixtureKrxEtpClient:
    def fetch_daily_etf_trades(self, trading_date: date) -> list[dict]:
        assert trading_date == _BASIS_DATE
        return _load_rows("etf_daily_20260716.json")

    def fetch_daily_etn_trades(self, trading_date: date) -> list[dict]:
        assert trading_date == _BASIS_DATE
        return _load_rows("etn_daily_20260716.json")


def _load_rows(fixture_name: str) -> list[dict]:
    payload = json.loads((_FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8"))
    return payload["OutBlock_1"]


def test_discovers_classifies_and_registers_single_stock_leveraged_universe() -> None:
    master = InstrumentMaster()

    result = discover_and_register_krx_leveraged_universe(
        _FixtureKrxEtpClient(),
        master,
        _BASIS_DATE,
        target_underlyings=frozenset({"SK하이닉스"}),
    )

    assert {product.source_symbol for product in result.products} == {
        "0193T0",
        "0197X0",
        "520101",
    }
    assert {product.underlying_name for product in result.products} == {"SK하이닉스"}
    assert {product.leverage_factor for product in result.products} == {
        Decimal("2"),
        Decimal("-2"),
    }
    assert {product.asset_class for product in result.products} == {
        AssetClass.LEVERAGED_ETF,
        AssetClass.LEVERAGED_ETN,
    }
    assert all(product.observation_status == "PRESENT_IN_DAILY_RESPONSE" for product in result.products)
    assert all(product.nav_or_indicative_value is not None for product in result.products)

    registered_ids = set(discover_leveraged_products(master, as_of_utc=0))
    assert registered_ids == {product.instrument_id for product in result.products}
    assert all(master.get_instrument(item).is_active for item in registered_ids)  # type: ignore[union-attr]


def test_non_single_stock_rows_are_ignored_and_malformed_marker_is_excluded() -> None:
    result = discover_and_register_krx_leveraged_universe(
        _FixtureKrxEtpClient(), InstrumentMaster(), _BASIS_DATE
    )

    assert "0177N0" not in {product.source_symbol for product in result.products}
    assert "580063" not in {product.source_symbol for product in result.products}
    assert len(result.exclusions) == 1
    assert result.exclusions[0].source_symbol == "BROKEN"
    assert "배율 marker" in result.exclusions[0].reason
