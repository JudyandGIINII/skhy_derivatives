"""결정론적 이벤트 기반 백테스트 엔진 최소판 (P1-05, FR-12, FR-16).

같은 이벤트 집합·config·seed·ordering_version으로 두 번 실행하면 이벤트
저널 해시와 최종 포트폴리오·체결 결과가 반드시 일치해야 한다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from skhy_research.domain.execution import OrderIntent, PaperFill
from skhy_research.domain.market import MarketQuote
from skhy_research.domain.simulation_event import SimulationEvent, sort_events
from skhy_research.engine.clock import SimulationClock
from skhy_research.engine.fill_model import OrderState, try_fill_leg
from skhy_research.engine.portfolio import PortfolioLedger


@dataclass
class BacktestRunResult:
    fills: list[PaperFill]
    portfolio: PortfolioLedger
    event_journal: list[dict[str, object]]
    event_journal_hash: str


def _journal_entry(event: SimulationEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "available_time_utc": event.available_time_utc,
        "event_time_utc": event.event_time_utc,
        "venue": event.venue,
        "event_type": event.event_type,
    }


def compute_journal_hash(journal: list[dict[str, object]]) -> str:
    canonical = json.dumps(journal, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_backtest(
    events: list[SimulationEvent],
    order_intents: list[OrderIntent],
    max_participation_rate: Decimal,
    seed: int,
) -> BacktestRunResult:
    ordered = sort_events(events)
    clock = SimulationClock(start_utc=ordered[0].available_time_utc if ordered else 0)
    portfolio = PortfolioLedger()
    journal: list[dict[str, object]] = []

    order_states = [
        OrderState(order=oi, leg_index=0, remaining_quantity=oi.legs[0].quantity)
        for oi in order_intents
    ]

    fill_counter = 0

    def _fill_id_factory() -> str:
        nonlocal fill_counter
        fill_counter += 1
        return f"fill-{seed}-{fill_counter}"

    all_fills: list[PaperFill] = []
    for event in ordered:
        clock.advance_to(event.available_time_utc)
        journal.append(_journal_entry(event))

        if event.event_type != "quote" or not isinstance(event.payload, MarketQuote):
            continue

        for state in order_states:
            if state.is_done:
                continue
            if state.order.legs[state.leg_index].instrument_id != event.payload.instrument_id:
                continue
            fill = try_fill_leg(state, event.payload, max_participation_rate, _fill_id_factory)
            if fill is None:
                continue
            all_fills.append(fill)
            portfolio.apply_fill(
                instrument_id=state.order.legs[state.leg_index].instrument_id,
                side=state.order.legs[state.leg_index].side.value,
                quantity=fill.filled_quantity,
                price=fill.fill_price,
                currency=event.payload.currency.value if event.payload.currency else "KRW",
            )

    return BacktestRunResult(
        fills=all_fills,
        portfolio=portfolio,
        event_journal=journal,
        event_journal_hash=compute_journal_hash(journal),
    )
