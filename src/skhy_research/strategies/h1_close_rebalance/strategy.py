"""H1 종가 리밸런싱 전략 플러그인 (P1-04, FR-09).

15:10 KST snapshot에서 신호를 만든다. 룩어헤드 차단은 `decide()` 호출 시
`assert_no_lookahead`로 강제하며, 위반하면 신호를 만들지 않고 즉시 예외를
던진다(값을 조용히 무시하지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from skhy_research.domain.calendar import utc_nanos_to_local_datetime
from skhy_research.domain.enums import SignalDirection, Venue
from skhy_research.domain.market import MarketPriceSnapshot
from skhy_research.domain.reference import FundSnapshot
from skhy_research.domain.strategy import Signal
from skhy_research.features.h1_close_pressure.close_pressure import (
    ORIGINAL_H1_LIVE_DATA_RESOLUTION,
    ORIGINAL_H1_PROMOTION_SCOPE,
    ClosePressureResult,
)
from skhy_research.strategies.h1_close_rebalance.decision_window import (
    H1_ORDER_INTENT_CUTOFF_KST,
    H1_SIGNAL_SNAPSHOT_TIME_KST,
    assert_live_decision_time,
    assert_order_intent_cutoff,
    build_decision_window,
)
from skhy_research.strategies.h1_close_rebalance.lookahead_guard import assert_no_lookahead

NO_SIGNAL_NEUTRAL_BAND = "close_pressure_within_neutral_band"
NO_SIGNAL_MISSING_REQUIRED_FLOW = "required_observable_flow_missing"


class H1ModelScopeMismatchError(RuntimeError):
    """feature의 promotion scope와 전략 scope가 달라 성과가 섞일 위험이 있을 때."""


@dataclass(frozen=True)
class H1Decision:
    signal: Signal | None
    no_signal_reason: str | None
    explain: dict[str, object]


class H1CloseRebalanceStrategy:
    strategy_id = "h1_close_rebalance"

    def __init__(
        self,
        strategy_version: str,
        neutral_band: Decimal,
        *,
        promotion_scope: str = ORIGINAL_H1_PROMOTION_SCOPE,
    ) -> None:
        self.strategy_version = strategy_version
        self._neutral_band = neutral_band
        self._promotion_scope = promotion_scope

    def decide(
        self,
        instrument_id: str,
        feature_set_id: str,
        close_pressure: ClosePressureResult,
        input_record_ids: list[str],
        fund_snapshots_used: list[FundSnapshot],
        decision_time_utc: int,
        expires_at_utc: int,
        signal_id: str,
        estimated_cost: Decimal,
        live_snapshots_used: list[MarketPriceSnapshot] | None = None,
    ) -> H1Decision:
        self._assert_model_scope(close_pressure)
        if close_pressure.promotion_scope == ORIGINAL_H1_PROMOTION_SCOPE:
            trading_date = utc_nanos_to_local_datetime(decision_time_utc, Venue.KRX).date()
            window = build_decision_window(
                trading_date,
                H1_SIGNAL_SNAPSHOT_TIME_KST,
                H1_ORDER_INTENT_CUTOFF_KST,
            )
            assert_live_decision_time(window, decision_time_utc)
            assert_order_intent_cutoff(window, expires_at_utc)
        self._assert_live_snapshot_lineage(close_pressure, input_record_ids, live_snapshots_used)
        assert_no_lookahead(fund_snapshots_used, decision_time_utc, live_snapshots_used)

        explain: dict[str, object] = {
            "close_pressure_value": str(close_pressure.value),
            "model_version": close_pressure.model_version,
            "data_resolution": close_pressure.data_resolution,
            "promotion_scope": close_pressure.promotion_scope,
            "promotion_eligible": close_pressure.promotion_eligible,
            "missing_flow_fund_ids": close_pressure.missing_flow_fund_ids,
            "missing_flow_inputs": tuple(
                (item.fund_id, item.fields) for item in close_pressure.missing_flow_inputs
            ),
            "neutral_band": str(self._neutral_band),
            "live_snapshot_record_ids": tuple(
                snapshot.record_id for snapshot in live_snapshots_used or []
            ),
        }

        if (
            close_pressure.promotion_scope == ORIGINAL_H1_PROMOTION_SCOPE
            and not close_pressure.promotion_eligible
        ):
            return H1Decision(
                signal=None,
                no_signal_reason=NO_SIGNAL_MISSING_REQUIRED_FLOW,
                explain=explain,
            )

        if abs(close_pressure.value) <= self._neutral_band:
            return H1Decision(signal=None, no_signal_reason=NO_SIGNAL_NEUTRAL_BAND, explain=explain)

        direction = SignalDirection.LONG if close_pressure.value > 0 else SignalDirection.SHORT
        gross_return = abs(close_pressure.value)
        net_return = gross_return - estimated_cost

        signal = Signal(
            signal_id=signal_id,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            instrument_id=instrument_id,
            direction=direction,
            confidence=min(Decimal("1"), abs(close_pressure.value) * Decimal("10")),
            expected_gross_return=gross_return,
            expected_cost=estimated_cost,
            expected_net_return=net_return,
            generated_at_utc=decision_time_utc,
            expires_at_utc=expires_at_utc,
            feature_set_id=feature_set_id,
            input_record_ids=input_record_ids,
        )
        return H1Decision(signal=signal, no_signal_reason=None, explain=explain)

    def _assert_model_scope(self, close_pressure: ClosePressureResult) -> None:
        if close_pressure.promotion_scope != self._promotion_scope:
            raise H1ModelScopeMismatchError(
                "close pressure와 strategy의 promotion scope가 다름: "
                f"feature={close_pressure.promotion_scope}, strategy={self._promotion_scope}"
            )
        if (
            not close_pressure.promotion_eligible
            and close_pressure.promotion_scope != ORIGINAL_H1_PROMOTION_SCOPE
            and self.strategy_version != close_pressure.model_version
        ):
            raise H1ModelScopeMismatchError(
                "승격 비대상 축소모델은 strategy_version을 model_version과 같게 분리해야 함: "
                f"strategy={self.strategy_version}, model={close_pressure.model_version}"
            )

    @staticmethod
    def _assert_live_snapshot_lineage(
        close_pressure: ClosePressureResult,
        input_record_ids: list[str],
        live_snapshots_used: list[MarketPriceSnapshot] | None,
    ) -> None:
        if close_pressure.data_resolution != ORIGINAL_H1_LIVE_DATA_RESOLUTION:
            return
        if not live_snapshots_used:
            raise H1ModelScopeMismatchError("live H1 feature에 live snapshot lineage가 없다")
        lineage = set(input_record_ids)
        missing = [item.record_id for item in live_snapshots_used if item.record_id not in lineage]
        if missing:
            raise H1ModelScopeMismatchError(f"live snapshot input_record_id 누락: {missing}")
