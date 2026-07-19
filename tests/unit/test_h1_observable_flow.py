"""PRD 9.1 observable flow의 결측과 실제 0 구분 검증."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.domain.enums import ReplicationType
from skhy_research.features.h1_close_pressure.observable_flow import (
    FlowObservation,
    ObservableFlowField,
    ObservableFlowInput,
    ReplicationFlowEvidence,
    calculate_observable_flow_adjustment,
)

_DECISION = 1_800_000_000_000_000_000


def _observed(value: str, record_id: str) -> FlowObservation:
    return FlowObservation(Decimal(value), _DECISION - 1, record_id)


def _replication() -> ReplicationFlowEvidence:
    return ReplicationFlowEvidence(
        ReplicationType.PHYSICAL,
        Decimal("0.5"),
        _DECISION - 1,
        "replication-record",
    )


def test_complete_observable_flow_applies_replication_and_preserves_zero() -> None:
    result = calculate_observable_flow_adjustment(
        ObservableFlowInput(
            close_auction_imbalance_notional=_observed("0", "auction-zero"),
            program_net_buy_notional=_observed("-20", "program"),
            net_creation_redemption_notional=_observed("10", "creation"),
            replication=_replication(),
        ),
        decision_time_utc=_DECISION,
    )

    assert result.value == Decimal("-15")
    assert result.replication_adjusted_creation_notional == Decimal("5")
    assert result.missing_fields == ()
    assert result.input_record_ids == (
        "auction-zero",
        "program",
        "creation",
        "replication-record",
    )


def test_missing_required_feed_returns_none_instead_of_partial_or_zero_sum() -> None:
    result = calculate_observable_flow_adjustment(
        ObservableFlowInput(
            close_auction_imbalance_notional=FlowObservation(
                None,
                None,
                None,
                "G-03 종가 예상체결 피드 미확보",
            ),
            program_net_buy_notional=_observed("20", "program"),
            net_creation_redemption_notional=_observed("10", "creation"),
            replication=_replication(),
        ),
        decision_time_utc=_DECISION,
    )

    assert result.value is None
    assert result.replication_adjusted_creation_notional is None
    assert result.missing_fields == (ObservableFlowField.CLOSE_AUCTION_IMBALANCE,)
    assert "auction" not in result.input_record_ids


def test_missing_program_feed_is_an_explicit_separate_missing_field() -> None:
    result = calculate_observable_flow_adjustment(
        ObservableFlowInput(
            close_auction_imbalance_notional=_observed("0", "auction"),
            program_net_buy_notional=FlowObservation(
                None,
                None,
                None,
                "G-03 프로그램매매 피드 미확보",
            ),
            net_creation_redemption_notional=_observed("10", "creation"),
            replication=_replication(),
        ),
        decision_time_utc=_DECISION,
    )

    assert result.value is None
    assert result.missing_fields == (ObservableFlowField.PROGRAM_NET_BUY,)


def test_flow_available_after_decision_is_rejected_as_lookahead() -> None:
    with pytest.raises(ValueError, match="decision 이후"):
        calculate_observable_flow_adjustment(
            ObservableFlowInput(
                close_auction_imbalance_notional=FlowObservation(
                    Decimal("1"), _DECISION + 1, "late-auction"
                ),
                program_net_buy_notional=_observed("2", "program"),
                net_creation_redemption_notional=_observed("3", "creation"),
                replication=_replication(),
            ),
            decision_time_utc=_DECISION,
        )
