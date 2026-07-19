"""중복·역순·gap·crossed quote 탐지 (FR-05, PRD 14.2)."""

from __future__ import annotations

from skhy_research.domain.enums import QualityFlag
from skhy_research.domain.market import MarketQuote


class SequenceState:
    """(source, instrument_id)별 마지막 event_time_utc와 dedupe_key 이력을 추적한다."""

    def __init__(self) -> None:
        self._last_event_time: dict[tuple[str, str], int] = {}
        self._seen_dedupe_keys: set[str] = set()

    def evaluate(
        self,
        key: tuple[str, str],
        event_time_utc: int,
        dedupe_key: str,
        max_gap_ns: int | None = None,
    ) -> list[QualityFlag]:
        if dedupe_key in self._seen_dedupe_keys:
            return [QualityFlag.DUPLICATE]
        self._seen_dedupe_keys.add(dedupe_key)

        flags: list[QualityFlag] = []
        last = self._last_event_time.get(key)
        if last is not None:
            if event_time_utc < last:
                flags.append(QualityFlag.OUT_OF_ORDER)
            elif max_gap_ns is not None and (event_time_utc - last) > max_gap_ns:
                flags.append(QualityFlag.GAP)

        self._last_event_time[key] = event_time_utc if last is None else max(last, event_time_utc)
        return flags


def detect_crossed_quote(quote: MarketQuote) -> bool:
    """매수호가가 매도호가보다 높은 비정상 상태 (PRD 14.2)."""
    return quote.bid_price > quote.ask_price
