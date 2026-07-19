"""Method-validation-only auxiliary backtest harness for proxy assets (PRD 10.2).

THIS MODULE IS STRICTLY FOR METHOD VALIDATION AND METHODOLOGICAL FEASIBILITY TESTING.
IT MUST NOT BE USED TO REPORT PORTFOLIO PERFORMANCE OR SIMULATED RESULTS AS SK HYNIX PERFORMANCE.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from skhy_research.domain.market import FXQuote, MarketQuote
from skhy_research.features.h2_adr_premium import compute_adr_premium


@dataclass(frozen=True)
class ProxyBacktestResult:
    """Results of the proxy backtest harness, clearly labeled as method validation only."""

    is_method_validation_only: bool
    target_asset_is_skhy: bool
    harness_label: str
    trades_executed: int
    total_pnl_usd: Decimal
    average_premium: Decimal
    has_warnings: bool
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.target_asset_is_skhy:
            raise ValueError(
                "CRITICAL ERROR: This result object is configured with SK Hynix as target asset. "
                "This harness is strictly method-validation-only and must not report SK Hynix performance."
            )
        if not self.is_method_validation_only:
            raise ValueError("CRITICAL ERROR: is_method_validation_only must be True.")
        if "METHOD_VALIDATION_ONLY" not in self.harness_label:
            raise ValueError("CRITICAL ERROR: harness_label must contain 'METHOD_VALIDATION_ONLY'.")


class ProxyMethodValidationHarness:
    """Harness to validate multi-currency pair logic using proxy assets.

    MUST NOT be used with SK Hynix assets.
    """

    def __init__(
        self,
        proxy_symbol: str,
        estimated_entry_cost_rate: Decimal,
        estimated_exit_cost_rate: Decimal,
    ) -> None:
        if "000660" in proxy_symbol or "SKHY" in proxy_symbol:
            raise ValueError(
                "This harness is method-validation-only and cannot be initialized with "
                f"SK Hynix symbols (000660/SKHY), got: {proxy_symbol}"
            )
        self.proxy_symbol = proxy_symbol
        self.estimated_entry_cost_rate = estimated_entry_cost_rate
        self.estimated_exit_cost_rate = estimated_exit_cost_rate

    def run_simulation(
        self,
        common_quotes: list[MarketQuote],
        adr_quotes: list[MarketQuote],
        fx_quotes: list[FXQuote],
        entry_threshold: Decimal = Decimal("0.05"),
        exit_threshold: Decimal = Decimal("0.01"),
    ) -> ProxyBacktestResult:
        """Run simulation over time-series quotes to validate the multi-currency arbitrage logic."""
        # Pre-validate input symbols to ensure no SK Hynix contamination
        for q in common_quotes:
            if "000660" in q.instrument_id or "000660" in q.symbol:
                raise ValueError("SK Hynix common stock found in input quotes.")
        for q in adr_quotes:
            if "SKHY" in q.instrument_id or "SKHY" in q.symbol:
                raise ValueError("SK Hynix ADR found in input quotes.")

        # Sort all quotes by timestamp to simulate tick-by-tick or snapshot-by-snapshot progression
        events: list[tuple[int, str, Any]] = []
        for cq in common_quotes:
            events.append((cq.event_time_utc, "common", cq))
        for aq in adr_quotes:
            events.append((aq.event_time_utc, "adr", aq))
        for fq in fx_quotes:
            events.append((fq.event_time_utc, "fx", fq))

        events.sort(key=lambda x: x[0])

        active_kr: MarketQuote | None = None
        active_adr: MarketQuote | None = None
        active_fx: FXQuote | None = None

        trades_executed = 0
        total_pnl_usd = Decimal("0")
        premium_sum = Decimal("0")
        premium_count = 0

        # Arbitrage position: 0 = Flat, 1 = Long Common / Short ADR
        position = 0
        entry_price_kr = Decimal("0")
        entry_price_adr = Decimal("0")
        entry_fx = Decimal("0")

        for _, qtype, quote in events:
            if qtype == "common":
                active_kr = quote
            elif qtype == "adr":
                active_adr = quote
            elif qtype == "fx":
                active_fx = quote

            # Need all components before computing premium
            if active_kr is None or active_adr is None or active_fx is None:
                continue

            res = compute_adr_premium(
                kr_common=active_kr,
                skhy_adr=active_adr,
                usdkrw=active_fx,
                estimated_entry_cost_rate=self.estimated_entry_cost_rate,
                estimated_exit_cost_rate=self.estimated_exit_cost_rate,
            )

            premium_sum += res.premium
            premium_count += 1

            if res.stale_reference:
                # Skip trading actions if either market reference is stale (market closed)
                continue

            if position == 0 and res.executable_entry_premium > entry_threshold:
                position = 1
                entry_price_kr = active_kr.ask_price
                entry_price_adr = active_adr.bid_price
                entry_fx = active_fx.bid
            elif position == 1 and res.executable_exit_premium <= exit_threshold:
                # Calculate PnL (USD-denominated)
                # Common leg (long entry, short exit): exit_bid - entry_ask in KRW
                common_pnl_krw = active_kr.bid_price - entry_price_kr
                common_pnl_usd = common_pnl_krw / active_fx.bid

                # ADR leg (short entry, long exit): entry_bid - exit_ask in USD (10 ADRs = 1 common)
                adr_pnl_usd = (entry_price_adr - active_adr.ask_price) * Decimal("10")

                # Costs: entry and exit rates applied to nominal value of positions
                entry_cost_usd = (
                    (entry_price_kr / entry_fx) * self.estimated_entry_cost_rate
                    + (entry_price_adr * Decimal("10")) * self.estimated_entry_cost_rate
                )
                exit_cost_usd = (
                    (active_kr.bid_price / active_fx.bid) * self.estimated_exit_cost_rate
                    + (active_adr.ask_price * Decimal("10")) * self.estimated_exit_cost_rate
                )

                trade_pnl = common_pnl_usd + adr_pnl_usd - entry_cost_usd - exit_cost_usd
                total_pnl_usd += trade_pnl
                trades_executed += 1
                position = 0

        avg_premium = premium_sum / Decimal(max(1, premium_count))

        warnings = (
            "WARNING: This is a method validation harness using proxy assets.",
            "WARNING: Do NOT report these results as SK Hynix performance.",
        )

        return ProxyBacktestResult(
            is_method_validation_only=True,
            target_asset_is_skhy=False,
            harness_label="METHOD_VALIDATION_ONLY_PROXIES",
            trades_executed=trades_executed,
            total_pnl_usd=total_pnl_usd,
            average_premium=avg_premium,
            has_warnings=True,
            warnings=warnings,
        )
