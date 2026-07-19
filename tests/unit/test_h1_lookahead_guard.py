"""P1-04 완료조건: 당일 장후 확정 AUM/NAV 주입 시 test가 실패한다 (룩어헤드 차단)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.domain.enums import AdjustmentStatus, Currency, ReplicationType, Session, Venue
from skhy_research.domain.reference import FundSnapshot
from skhy_research.strategies.h1_close_rebalance.lookahead_guard import (
    LookaheadViolationError,
    assert_no_lookahead,
)

_DECISION_TIME = 1_800_000_000_000_000_000


def _snapshot(fund_id: str, published_at: int) -> FundSnapshot:
    return FundSnapshot(
        source="hkex_issuer",
        venue=Venue.HKEX,
        symbol="7709",
        event_time_utc=published_at,
        received_time_utc=published_at,
        currency=Currency.HKD,
        session=Session.REFERENCE,
        is_delayed=False,
        adjustment_status=AdjustmentStatus.NOT_APPLICABLE,
        fund_id=fund_id,
        leverage_beta=Decimal("2"),
        aum=Decimal("1000000"),
        nav=Decimal("10.5"),
        replication_type=ReplicationType.SWAP,
        published_at=published_at,
        effective_at=published_at,
    )


def test_snapshot_published_before_decision_time_is_allowed() -> None:
    snapshot = _snapshot("FUND_A", _DECISION_TIME - 1_000_000_000)
    assert_no_lookahead([snapshot], _DECISION_TIME)  # 예외 없이 통과


def test_snapshot_published_exactly_at_decision_time_is_rejected() -> None:
    snapshot = _snapshot("FUND_A", _DECISION_TIME)
    with pytest.raises(LookaheadViolationError, match="FUND_A"):
        assert_no_lookahead([snapshot], _DECISION_TIME)


def test_snapshot_published_after_decision_time_is_rejected() -> None:
    """당일 장 종료 후 확정되는 NAV/AUM을 같은 날 신호에 사용하는 전형적 룩어헤드 케이스."""
    same_day_close_confirmed = _snapshot("FUND_A", _DECISION_TIME + 5_000_000_000)
    with pytest.raises(LookaheadViolationError):
        assert_no_lookahead([same_day_close_confirmed], _DECISION_TIME)


def test_one_violating_snapshot_among_many_is_still_caught() -> None:
    good = _snapshot("FUND_GOOD", _DECISION_TIME - 1_000_000_000)
    bad = _snapshot("FUND_BAD", _DECISION_TIME + 1_000_000_000)
    with pytest.raises(LookaheadViolationError, match="FUND_BAD"):
        assert_no_lookahead([good, bad], _DECISION_TIME)
