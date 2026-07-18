"""H1 종가 리밸런싱 전략 플러그인 (P1-04, FR-09).

15:10 KST snapshot에서 신호를 만든다. 룩어헤드 차단은 `decide()` 호출 시
`assert_no_lookahead`로 강제하며, 위반하면 신호를 만들지 않고 즉시 예외를
던진다(값을 조용히 무시하지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from skhy_research.domain.enums import SignalDirection
from skhy_research.domain.reference import FundSnapshot
from skhy_research.domain.strategy import Signal
from skhy_research.features.h1_close_pressure.close_pressure import ClosePressureResult
from skhy_research.strategies.h1_close_rebalance.lookahead_guard import assert_no_lookahead

NO_SIGNAL_NEUTRAL_BAND = "close_pressure_within_neutral_band"


@dataclass(frozen=True)
class H1Decision:
    signal: Signal | None
    no_signal_reason: str | None
    explain: dict[str, object]


class H1CloseRebalanceStrategy:
    strategy_id = "h1_close_rebalance"

    def __init__(self, strategy_version: str, neutral_band: Decimal) -> None:
        self.strategy_version = strategy_version
        self._neutral_band = neutral_band

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
    ) -> H1Decision:
        assert_no_lookahead(fund_snapshots_used, decision_time_utc)

        explain: dict[str, object] = {
            "close_pressure_value": str(close_pressure.value),
            "model_version": close_pressure.model_version,
            "missing_flow_fund_ids": close_pressure.missing_flow_fund_ids,
            "neutral_band": str(self._neutral_band),
        }

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
