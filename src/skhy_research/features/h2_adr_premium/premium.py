"""H2 ADR-premium feature computation (PRD 5.1).

Computes display fair value, premium, and executable entry/exit premium.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from skhy_research.domain.enums import QualityFlag, Session
from skhy_research.domain.market import FXQuote, MarketQuote


@dataclass(frozen=True)
class ADRPremiumResult:
    adr_fair_usd: Decimal
    premium: Decimal
    executable_entry_premium: Decimal
    executable_exit_premium: Decimal
    stale_reference: bool


def compute_adr_premium(
    kr_common: MarketQuote,
    skhy_adr: MarketQuote,
    usdkrw: FXQuote,
    estimated_entry_cost_rate: Decimal,
    estimated_exit_cost_rate: Decimal,
    force_stale: bool = False,
) -> ADRPremiumResult:
    """Compute H2 ADR premium metrics per PRD Section 5.1.

    Args:
        kr_common: MarketQuote for SK Hynix common stock on KRX (KRW)
        skhy_adr: MarketQuote for Nasdaq SKHY ADR (USD)
        usdkrw: FXQuote for USD/KRW
        estimated_entry_cost_rate: Entry execution cost rate
        estimated_exit_cost_rate: Exit execution cost rate
        force_stale: If True, forces stale_reference to be True

    Returns:
        ADRPremiumResult with computed premiums and stale reference flag.
    """
    if usdkrw.pair != "USD/KRW":
        raise ValueError(f"FXQuote pair must be USD/KRW, got {usdkrw.pair}")

    # Enforce non-zero denominators
    kr_mid = (kr_common.bid_price + kr_common.ask_price) / Decimal("2")
    usdkrw_mid = (usdkrw.bid + usdkrw.ask) / Decimal("2")
    skhy_mid = (skhy_adr.bid_price + skhy_adr.ask_price) / Decimal("2")

    if usdkrw_mid == 0:
        raise ValueError("USD/KRW FX rate midpoint cannot be zero")
    if kr_mid == 0:
        raise ValueError("KR common stock midpoint price cannot be zero")
    if kr_common.ask_price == 0:
        raise ValueError("KR common stock ask price cannot be zero for entry premium calculation")
    if kr_common.bid_price == 0:
        raise ValueError("KR common stock bid price cannot be zero for exit premium calculation")

    # adr_fair_usd = kr_common_krw / usdkrw / 10
    adr_fair_usd = kr_mid / usdkrw_mid / Decimal("10")

    if adr_fair_usd == 0:
        raise ValueError("ADR fair value USD cannot be zero for premium calculation")

    # premium = skhy_usd / adr_fair_usd - 1
    premium = skhy_mid / adr_fair_usd - Decimal("1")

    # executable_entry_premium = (skhy_bid_usd * 10 * usdkrw_bid - kr_ask_krw) / kr_ask_krw - estimated_entry_cost_rate
    executable_entry_premium = (
        (skhy_adr.bid_price * Decimal("10") * usdkrw.bid - kr_common.ask_price) / kr_common.ask_price
    ) - estimated_entry_cost_rate

    # executable_exit_premium = (skhy_ask_usd * 10 * usdkrw_ask - kr_bid_krw) / kr_bid_krw + estimated_exit_cost_rate
    executable_exit_premium = (
        (skhy_adr.ask_price * Decimal("10") * usdkrw.ask - kr_common.bid_price) / kr_common.bid_price
    ) + estimated_exit_cost_rate

    # stale_reference logic
    is_closed = (
        kr_common.session == Session.CLOSED
        or skhy_adr.session == Session.CLOSED
        or usdkrw.session == Session.CLOSED
        or QualityFlag.MARKET_CLOSED in kr_common.quality_flag
        or QualityFlag.MARKET_CLOSED in skhy_adr.quality_flag
        or QualityFlag.MARKET_CLOSED in usdkrw.quality_flag
        or QualityFlag.STALE in kr_common.quality_flag
        or QualityFlag.STALE in skhy_adr.quality_flag
        or QualityFlag.STALE in usdkrw.quality_flag
    )
    stale_reference = is_closed or force_stale

    return ADRPremiumResult(
        adr_fair_usd=adr_fair_usd,
        premium=premium,
        executable_entry_premium=executable_entry_premium,
        executable_exit_premium=executable_exit_premium,
        stale_reference=stale_reference,
    )
