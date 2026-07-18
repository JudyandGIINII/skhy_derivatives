"""P1-04 검증: H1CloseRebalanceStrategy의 신호 생성·no-signal·룩어헤드 전파."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    ReplicationType,
    Session,
    SignalDirection,
    Venue,
)
from skhy_research.domain.reference import FundSnapshot
from skhy_research.features.h1_close_pressure.close_pressure import ClosePressureResult
from skhy_research.strategies.h1_close_rebalance.lookahead_guard import LookaheadViolationError
from skhy_research.strategies.h1_close_rebalance.strategy import (
    NO_SIGNAL_NEUTRAL_BAND,
    H1CloseRebalanceStrategy,
)

_DECISION_TIME = 1_800_000_000_000_000_000
_EXPIRES = _DECISION_TIME + 60_000_000_000


def _strategy() -> H1CloseRebalanceStrategy:
    return H1CloseRebalanceStrategy(strategy_version="1.0.0", neutral_band=Decimal("0.001"))


def _pressure(value: str, model_version: str = "full") -> ClosePressureResult:
    return ClosePressureResult(Decimal(value), model_version, ())


def _snapshot(published_at: int) -> FundSnapshot:
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
        fund_id="HKEX_7709",
        leverage_beta=Decimal("2"),
        aum=Decimal("1000000"),
        nav=Decimal("10.5"),
        replication_type=ReplicationType.SWAP,
        published_at=published_at,
        effective_at=published_at,
    )


def test_pressure_within_neutral_band_produces_no_signal() -> None:
    strategy = _strategy()
    decision = strategy.decide(
        instrument_id="000660",
        feature_set_id="h1_close_pressure@1.0.0",
        close_pressure=_pressure("0.0005"),
        input_record_ids=["rec-1"],
        fund_snapshots_used=[_snapshot(_DECISION_TIME - 1_000_000_000)],
        decision_time_utc=_DECISION_TIME,
        expires_at_utc=_EXPIRES,
        signal_id="sig-1",
        estimated_cost=Decimal("0.0001"),
    )
    assert decision.signal is None
    assert decision.no_signal_reason == NO_SIGNAL_NEUTRAL_BAND


def test_positive_pressure_beyond_band_produces_long_signal() -> None:
    strategy = _strategy()
    decision = strategy.decide(
        instrument_id="000660",
        feature_set_id="h1_close_pressure@1.0.0",
        close_pressure=_pressure("0.004"),
        input_record_ids=["rec-1"],
        fund_snapshots_used=[_snapshot(_DECISION_TIME - 1_000_000_000)],
        decision_time_utc=_DECISION_TIME,
        expires_at_utc=_EXPIRES,
        signal_id="sig-2",
        estimated_cost=Decimal("0.001"),
    )
    assert decision.signal is not None
    assert decision.signal.direction == SignalDirection.LONG
    assert decision.signal.expected_gross_return == Decimal("0.004")
    assert decision.signal.expected_net_return == Decimal("0.003")
    assert Decimal("0") <= decision.signal.confidence <= Decimal("1")


def test_negative_pressure_beyond_band_produces_short_signal() -> None:
    strategy = _strategy()
    decision = strategy.decide(
        instrument_id="000660",
        feature_set_id="h1_close_pressure@1.0.0",
        close_pressure=_pressure("-0.004"),
        input_record_ids=["rec-1"],
        fund_snapshots_used=[],
        decision_time_utc=_DECISION_TIME,
        expires_at_utc=_EXPIRES,
        signal_id="sig-3",
        estimated_cost=Decimal("0.001"),
    )
    assert decision.signal is not None
    assert decision.signal.direction == SignalDirection.SHORT


def test_confidence_is_clamped_to_one_for_extreme_pressure() -> None:
    strategy = _strategy()
    decision = strategy.decide(
        instrument_id="000660",
        feature_set_id="h1_close_pressure@1.0.0",
        close_pressure=_pressure("5.0"),  # 극단값
        input_record_ids=["rec-1"],
        fund_snapshots_used=[],
        decision_time_utc=_DECISION_TIME,
        expires_at_utc=_EXPIRES,
        signal_id="sig-4",
        estimated_cost=Decimal("0.001"),
    )
    assert decision.signal is not None
    assert decision.signal.confidence == Decimal("1")


def test_lookahead_violation_propagates_and_blocks_signal_generation() -> None:
    strategy = _strategy()
    with pytest.raises(LookaheadViolationError):
        strategy.decide(
            instrument_id="000660",
            feature_set_id="h1_close_pressure@1.0.0",
            close_pressure=_pressure("0.004"),
            input_record_ids=["rec-1"],
            fund_snapshots_used=[_snapshot(_DECISION_TIME + 1_000_000_000)],  # 당일 장후 확정치
            decision_time_utc=_DECISION_TIME,
            expires_at_utc=_EXPIRES,
            signal_id="sig-5",
            estimated_cost=Decimal("0.001"),
        )


def test_explain_dict_includes_model_version_and_missing_funds() -> None:
    strategy = _strategy()
    reduced_pressure = ClosePressureResult(Decimal("0.004"), "reduced", ("FUND_X",))
    decision = strategy.decide(
        instrument_id="000660",
        feature_set_id="h1_close_pressure@1.0.0",
        close_pressure=reduced_pressure,
        input_record_ids=["rec-1"],
        fund_snapshots_used=[],
        decision_time_utc=_DECISION_TIME,
        expires_at_utc=_EXPIRES,
        signal_id="sig-6",
        estimated_cost=Decimal("0.001"),
    )
    assert decision.explain["model_version"] == "reduced"
    assert decision.explain["missing_flow_fund_ids"] == ("FUND_X",)
