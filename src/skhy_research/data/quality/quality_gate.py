"""정규화 레코드에 품질 플래그를 부여하고 신호 차단 여부를 판정한다 (FR-05).

`blocks_signal=True`인 레코드는 신규 전략 신호 생성 입력에서 제외되어야 한다
(리스크 엔진이 최종 게이트를 담당하지만, 이 판정을 그대로 사용한다).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from skhy_research.data.quality.detectors import SequenceState, detect_crossed_quote
from skhy_research.data.reconciliation.divergence import (
    check_source_divergence,
    check_stale_reference,
)
from skhy_research.domain.enums import QualityFlag
from skhy_research.domain.market import MarketQuote

_BLOCKING_FLAGS = frozenset(
    {
        QualityFlag.SOURCE_DIVERGENCE,
        QualityFlag.STALE,
        QualityFlag.MARKET_CLOSED,
        QualityFlag.HALTED,
        QualityFlag.DUPLICATE,
        QualityFlag.OUT_OF_ORDER,
        QualityFlag.UNKNOWN_CONVERSION,
        QualityFlag.BORROW_UNAVAILABLE,
    }
)


@dataclass(frozen=True)
class QualityEvaluation:
    flags: frozenset[QualityFlag]
    is_crossed_quote: bool
    blocks_signal: bool


class QualityGate:
    def __init__(self, max_gap_ns: int | None = None) -> None:
        self._sequence_state = SequenceState()
        self._max_gap_ns = max_gap_ns

    def evaluate_quote(self, quote: MarketQuote, dedupe_key: str) -> QualityEvaluation:
        flags: set[QualityFlag] = set(quote.quality_flag)
        key = (quote.source, quote.instrument_id)
        flags.update(
            self._sequence_state.evaluate(key, quote.event_time_utc, dedupe_key, self._max_gap_ns)
        )
        crossed = detect_crossed_quote(quote)
        blocks = bool(flags & _BLOCKING_FLAGS) or crossed
        return QualityEvaluation(frozenset(flags), crossed, blocks)

    def evaluate_cross_source(
        self,
        primary: MarketQuote,
        secondary: MarketQuote,
        tolerance_pct: Decimal,
        max_time_skew_ns: int,
    ) -> QualityEvaluation:
        flags: set[QualityFlag] = set(primary.quality_flag)
        if check_source_divergence(primary, secondary, tolerance_pct, max_time_skew_ns):
            flags.add(QualityFlag.SOURCE_DIVERGENCE)
        blocks = bool(flags & _BLOCKING_FLAGS)
        return QualityEvaluation(frozenset(flags), False, blocks)

    def evaluate_staleness(
        self, quote: MarketQuote, as_of_utc: int, max_age_ns: int
    ) -> QualityEvaluation:
        flags: set[QualityFlag] = set(quote.quality_flag)
        if check_stale_reference(quote, as_of_utc, max_age_ns):
            flags.add(QualityFlag.STALE)
        blocks = bool(flags & _BLOCKING_FLAGS)
        return QualityEvaluation(frozenset(flags), False, blocks)
