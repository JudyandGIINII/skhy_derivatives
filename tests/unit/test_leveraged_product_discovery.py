"""P1-02 검증: 레버리지 상품 동적 발견 (정적 종목 목록 고정 금지, PRD 6장)."""

from __future__ import annotations

from skhy_research.application.fund_snapshot_collection import discover_leveraged_products
from skhy_research.application.instrument_master import InstrumentMaster
from skhy_research.domain.enums import AssetClass, Venue
from skhy_research.domain.instrument import InstrumentRecord

_T0 = 1_700_000_000_000_000_000
_T1 = 1_800_000_000_000_000_000


def test_discovers_only_active_leveraged_asset_classes() -> None:
    master = InstrumentMaster()
    master.register_instrument(
        InstrumentRecord(
            instrument_id="COMMON",
            asset_class=AssetClass.COMMON_STOCK,
            primary_venue=Venue.KRX,
            display_name="본주",
            is_active=True,
            listed_at_utc=_T0,
        )
    )
    master.register_instrument(
        InstrumentRecord(
            instrument_id="LEV_ETF",
            asset_class=AssetClass.LEVERAGED_ETF,
            primary_venue=Venue.KRX,
            display_name="레버리지 ETF",
            is_active=True,
            listed_at_utc=_T0,
        )
    )
    master.register_instrument(
        InstrumentRecord(
            instrument_id="HKEX_SWAP",
            asset_class=AssetClass.SWAP_PRODUCT,
            primary_venue=Venue.HKEX,
            display_name="HKEX 7709",
            is_active=True,
            listed_at_utc=_T0,
        )
    )
    master.register_instrument(
        InstrumentRecord(
            instrument_id="DELISTED_ETN",
            asset_class=AssetClass.LEVERAGED_ETN,
            primary_venue=Venue.KRX,
            display_name="상장폐지된 ETN",
            is_active=False,
            listed_at_utc=_T0,
            delisted_at_utc=_T1,
        )
    )

    discovered = discover_leveraged_products(master, as_of_utc=_T1 + 1)

    assert set(discovered) == {"LEV_ETF", "HKEX_SWAP"}  # COMMON은 제외, 상장폐지 ETN도 제외


def test_discovers_empty_when_no_leveraged_products_registered() -> None:
    master = InstrumentMaster()
    master.register_instrument(
        InstrumentRecord(
            instrument_id="COMMON",
            asset_class=AssetClass.COMMON_STOCK,
            primary_venue=Venue.KRX,
            display_name="본주",
            is_active=True,
            listed_at_utc=_T0,
        )
    )
    assert discover_leveraged_products(master, as_of_utc=_T1) == []
