"""내부 instrument master 조회 (P0-05).

인메모리 구현이며 P1-01에서 KRX/발행사 기준정보로 채운다. 실제 시각 기준
alias 해석에 필요한 로직(심볼 변경·상장폐지 대응)을 여기서 확정한다.
"""

from __future__ import annotations

from skhy_research.domain.enums import Venue
from skhy_research.domain.instrument import CorporateActionRecord, InstrumentRecord, SymbolAlias


class UnresolvedInstrumentError(RuntimeError):
    pass


class AmbiguousAliasError(RuntimeError):
    """같은 시점에 유효구간이 겹치는 alias가 두 개 이상 존재 — 데이터 오류다."""


class InstrumentMaster:
    def __init__(self) -> None:
        self._instruments: dict[str, InstrumentRecord] = {}
        self._aliases: list[SymbolAlias] = []
        self._corporate_actions: dict[str, list[CorporateActionRecord]] = {}

    def register_instrument(self, instrument: InstrumentRecord) -> None:
        self._instruments[instrument.instrument_id] = instrument

    def register_alias(self, alias: SymbolAlias) -> None:
        if alias.instrument_id not in self._instruments:
            raise UnresolvedInstrumentError(
                f"instrument_id={alias.instrument_id}가 먼저 register_instrument로 등록되어야 한다"
            )
        overlapping = [
            existing
            for existing in self._aliases
            if existing.source == alias.source
            and existing.venue == alias.venue
            and existing.symbol == alias.symbol
            and _ranges_overlap(existing, alias)
        ]
        if overlapping:
            raise AmbiguousAliasError(
                f"{alias.source}/{alias.venue}/{alias.symbol}의 유효구간이 기존 alias와 겹친다"
            )
        self._aliases.append(alias)

    def register_corporate_action(self, action: CorporateActionRecord) -> None:
        if action.instrument_id not in self._instruments:
            raise UnresolvedInstrumentError(
                f"instrument_id={action.instrument_id}가 먼저 register_instrument로 등록되어야 한다"
            )
        self._corporate_actions.setdefault(action.instrument_id, []).append(action)

    def resolve_instrument_id(
        self, source: str, venue: Venue, symbol: str, as_of_utc: int
    ) -> str:
        matches = [
            alias
            for alias in self._aliases
            if alias.source == source
            and alias.venue == venue
            and alias.symbol == symbol
            and alias.covers(as_of_utc)
        ]
        if not matches:
            raise UnresolvedInstrumentError(
                f"{source}/{venue}/{symbol}에 대해 {as_of_utc} 시점 유효한 alias가 없다"
            )
        if len(matches) > 1:
            raise AmbiguousAliasError(
                f"{source}/{venue}/{symbol}의 {as_of_utc} 시점에 alias가 {len(matches)}개 매칭됨"
            )
        return matches[0].instrument_id

    def get_instrument(self, instrument_id: str) -> InstrumentRecord | None:
        return self._instruments.get(instrument_id)

    def list_instruments(self) -> list[InstrumentRecord]:
        return list(self._instruments.values())

    def is_active_as_of(self, instrument_id: str, as_of_utc: int) -> bool:
        instrument = self._instruments.get(instrument_id)
        if instrument is None:
            return False
        if instrument.listed_at_utc is not None and as_of_utc < instrument.listed_at_utc:
            return False
        return not (
            instrument.delisted_at_utc is not None and as_of_utc >= instrument.delisted_at_utc
        )

    def corporate_actions_as_of(
        self, instrument_id: str, as_of_utc: int
    ) -> list[CorporateActionRecord]:
        """as_of_utc 시점까지 발효된 조정 계수를 effective_date 오름차순으로 반환한다."""
        actions = self._corporate_actions.get(instrument_id, [])
        applicable = [a for a in actions if a.effective_date_utc <= as_of_utc]
        return sorted(applicable, key=lambda a: (a.effective_date_utc, a.version))


def _ranges_overlap(a: SymbolAlias, b: SymbolAlias) -> bool:
    a_end = a.effective_to_utc if a.effective_to_utc is not None else float("inf")
    b_end = b.effective_to_utc if b.effective_to_utc is not None else float("inf")
    return a.effective_from_utc < b_end and b.effective_from_utc < a_end
