"""P0-05 검증: instrument master의 alias 시점 해석·중복 방지·기업행사 버전 정렬."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.application.instrument_master import (
    AmbiguousAliasError,
    InstrumentMaster,
    UnresolvedInstrumentError,
)
from skhy_research.domain.enums import Venue
from skhy_research.domain.instrument import CorporateActionRecord, InstrumentRecord, SymbolAlias

_T0 = 1_700_000_000_000_000_000
_T1 = 1_750_000_000_000_000_000
_T2 = 1_800_000_000_000_000_000


def _instrument(instrument_id: str = "SKHY_000660_COMMON") -> InstrumentRecord:
    return InstrumentRecord(
        instrument_id=instrument_id,
        asset_class="COMMON_STOCK",
        primary_venue=Venue.KRX,
        display_name="SK hynix",
        is_active=True,
        listed_at_utc=_T0,
    )


def test_register_alias_requires_known_instrument() -> None:
    master = InstrumentMaster()
    with pytest.raises(UnresolvedInstrumentError):
        master.register_alias(
            SymbolAlias(
                instrument_id="unknown",
                source="KRX",
                venue=Venue.KRX,
                symbol="000660",
                effective_from_utc=_T0,
            )
        )


def test_resolve_instrument_id_by_symbol_and_time() -> None:
    master = InstrumentMaster()
    master.register_instrument(_instrument())
    master.register_alias(
        SymbolAlias(
            instrument_id="SKHY_000660_COMMON",
            source="KRX",
            venue=Venue.KRX,
            symbol="000660",
            effective_from_utc=_T0,
        )
    )

    resolved = master.resolve_instrument_id("KRX", Venue.KRX, "000660", _T1)
    assert resolved == "SKHY_000660_COMMON"


def test_resolve_instrument_id_raises_when_no_alias_covers_timestamp() -> None:
    master = InstrumentMaster()
    master.register_instrument(_instrument())
    master.register_alias(
        SymbolAlias(
            instrument_id="SKHY_000660_COMMON",
            source="KRX",
            venue=Venue.KRX,
            symbol="000660",
            effective_from_utc=_T1,
        )
    )

    with pytest.raises(UnresolvedInstrumentError):
        master.resolve_instrument_id("KRX", Venue.KRX, "000660", _T0)  # T1 이전이라 매칭 없음


def test_symbol_change_is_resolved_by_effective_range() -> None:
    """심볼이 바뀌어도 시점에 맞는 alias로 같은 instrument_id를 찾는다."""
    master = InstrumentMaster()
    master.register_instrument(_instrument())
    master.register_alias(
        SymbolAlias(
            instrument_id="SKHY_000660_COMMON",
            source="KRX",
            venue=Venue.KRX,
            symbol="OLDSYMBOL",
            effective_from_utc=_T0,
            effective_to_utc=_T1,
        )
    )
    master.register_alias(
        SymbolAlias(
            instrument_id="SKHY_000660_COMMON",
            source="KRX",
            venue=Venue.KRX,
            symbol="000660",
            effective_from_utc=_T1,
        )
    )

    assert master.resolve_instrument_id("KRX", Venue.KRX, "OLDSYMBOL", _T0) == "SKHY_000660_COMMON"
    assert master.resolve_instrument_id("KRX", Venue.KRX, "000660", _T2) == "SKHY_000660_COMMON"


def test_overlapping_alias_ranges_are_rejected() -> None:
    master = InstrumentMaster()
    master.register_instrument(_instrument())
    master.register_alias(
        SymbolAlias(
            instrument_id="SKHY_000660_COMMON",
            source="KRX",
            venue=Venue.KRX,
            symbol="000660",
            effective_from_utc=_T0,
        )
    )

    with pytest.raises(AmbiguousAliasError):
        master.register_alias(
            SymbolAlias(
                instrument_id="SKHY_000660_COMMON",
                source="KRX",
                venue=Venue.KRX,
                symbol="000660",
                effective_from_utc=_T1,  # 기존 alias(무한)와 겹침
            )
        )


def test_is_active_as_of_respects_listing_and_delisting() -> None:
    master = InstrumentMaster()
    instrument = InstrumentRecord(
        instrument_id="DELISTED_ETN",
        asset_class="LEVERAGED_ETN",
        primary_venue=Venue.KRX,
        display_name="temp",
        is_active=False,
        listed_at_utc=_T0,
        delisted_at_utc=_T1,
    )
    master.register_instrument(instrument)

    assert master.is_active_as_of("DELISTED_ETN", _T0) is True
    assert master.is_active_as_of("DELISTED_ETN", _T1) is False
    assert master.is_active_as_of("DELISTED_ETN", _T0 - 1) is False


def test_corporate_actions_are_returned_sorted_and_filtered_by_as_of() -> None:
    master = InstrumentMaster()
    master.register_instrument(_instrument())
    master.register_corporate_action(
        CorporateActionRecord(
            instrument_id="SKHY_000660_COMMON",
            action_type="SPLIT",
            effective_date_utc=_T1,
            adjustment_factor=Decimal("0.5"),
            version=1,
            announced_at_utc=_T0,
        )
    )
    master.register_corporate_action(
        CorporateActionRecord(
            instrument_id="SKHY_000660_COMMON",
            action_type="DIVIDEND",
            effective_date_utc=_T0,
            adjustment_factor=Decimal("0.99"),
            version=1,
            announced_at_utc=_T0,
        )
    )

    actions_at_t1 = master.corporate_actions_as_of("SKHY_000660_COMMON", _T1)
    assert [a.action_type for a in actions_at_t1] == ["DIVIDEND", "SPLIT"]

    actions_before_any = master.corporate_actions_as_of("SKHY_000660_COMMON", _T0 - 1)
    assert actions_before_any == []


def test_corporate_action_rejects_non_positive_adjustment_factor() -> None:
    with pytest.raises(ValueError, match="adjustment_factor"):
        CorporateActionRecord(
            instrument_id="SKHY_000660_COMMON",
            action_type="SPLIT",
            effective_date_utc=_T0,
            adjustment_factor=Decimal("0"),
            version=1,
            announced_at_utc=_T0,
        )
