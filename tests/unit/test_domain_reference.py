"""P0-04 검증: FundSnapshot/ConversionStatus/BorrowQuote 불변조건."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from skhy_research.domain.enums import (
    AdjustmentStatus,
    ConversionStatusValue,
    Currency,
    ReplicationType,
    Session,
    Venue,
)
from skhy_research.domain.reference import BorrowQuote, ConversionStatus, FundSnapshot

_NOW = 1_800_000_000_000_000_000


def _envelope_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        source="HKEX_ISSUER",
        venue=Venue.HKEX,
        symbol="7709",
        event_time_utc=_NOW,
        received_time_utc=_NOW,
        currency=Currency.HKD,
        session=Session.REFERENCE,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.NOT_APPLICABLE,
    )
    base.update(overrides)
    return base


def test_fund_snapshot_requires_method_when_net_creation_present() -> None:
    with pytest.raises(ValidationError, match="추정방법"):
        FundSnapshot(
            **_envelope_kwargs(),
            fund_id="HKEX_7709",
            leverage_beta=Decimal("2"),
            aum=Decimal("1000000"),
            nav=Decimal("10.5"),
            net_creation_estimate=Decimal("5000"),
            net_creation_estimate_method=None,
            replication_type=ReplicationType.SWAP,
            published_at=_NOW,
            effective_at=_NOW,
        )


def test_fund_snapshot_swap_replication_is_representable() -> None:
    snapshot = FundSnapshot(
        **_envelope_kwargs(),
        fund_id="HKEX_7709",
        leverage_beta=Decimal("2"),
        aum=Decimal("1000000"),
        nav=Decimal("10.5"),
        replication_type=ReplicationType.SWAP,
        published_at=_NOW,
        effective_at=_NOW,
    )
    assert snapshot.replication_type == ReplicationType.SWAP


def test_conversion_status_operational_requires_full_evidence() -> None:
    with pytest.raises(ValidationError, match="OPERATIONAL"):
        ConversionStatus(
            **_envelope_kwargs(venue=Venue.REFERENCE, symbol="SKHY_CONVERSION"),
            status=ConversionStatusValue.OPERATIONAL,
            adr_ratio_common_to_adr=Decimal("10"),
            evidence_url="https://example.com/citi-notice",
            confirmed_at_utc=_NOW,
        )


def test_conversion_status_operational_with_full_evidence() -> None:
    status = ConversionStatus(
        **_envelope_kwargs(venue=Venue.REFERENCE, symbol="SKHY_CONVERSION"),
        status=ConversionStatusValue.OPERATIONAL,
        adr_ratio_common_to_adr=Decimal("10"),
        min_quantity=Decimal("1"),
        fee_description="건당 5,000원",
        estimated_settlement_days=3,
        evidence_url="https://example.com/citi-notice",
        confirmed_at_utc=_NOW,
    )
    assert status.status == ConversionStatusValue.OPERATIONAL


def test_conversion_status_unknown_does_not_require_evidence_fields() -> None:
    status = ConversionStatus(
        **_envelope_kwargs(venue=Venue.REFERENCE, symbol="SKHY_CONVERSION"),
        status=ConversionStatusValue.UNKNOWN,
        adr_ratio_common_to_adr=Decimal("10"),
        evidence_url="https://example.com/pending",
        confirmed_at_utc=_NOW,
    )
    assert status.min_quantity is None


def test_borrow_quote_basic_construction() -> None:
    quote = BorrowQuote(
        **_envelope_kwargs(venue=Venue.NASDAQ, symbol="SKHY", currency=Currency.USD),
        instrument_id="SKHY",
        available_quantity=Decimal("50000"),
        annualized_rate_pct=Decimal("3.5"),
        provider="INTERACTIVE_BROKERS",
        valid_until_utc=_NOW + 60_000_000_000,
    )
    assert quote.annualized_rate_pct == Decimal("3.5")
