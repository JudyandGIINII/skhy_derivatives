"""GateDecision의 append-only PostgreSQL 저장소.

`docs/decisions/gates/*.md`는 검토자를 위한 근거·맥락 문서다. 런타임 gate 상태의
기계용 진실의 출처는 이 저장소의 `gate_decision` journal이며 Markdown을 파싱하지
않는다.
"""

from __future__ import annotations

from sqlalchemy import Engine, and_, func, insert, select

from skhy_research.adapters.persistence.schema import gate_decision
from skhy_research.domain.gate import GateDecision


class PostgresGateDecisionStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save_decision(self, decision: GateDecision) -> None:
        """결정을 append-only로 저장한다; 같은 gate·기록시각은 DB가 거부한다."""
        with self._engine.begin() as conn:
            conn.execute(insert(gate_decision).values(**decision.model_dump(mode="json")))

    def load_all_decisions(self) -> list[GateDecision]:
        """각 gate에서 `recorded_at_utc`가 가장 최신인 결정 하나씩을 반환한다."""
        latest = (
            select(
                gate_decision.c.gate_id,
                func.max(gate_decision.c.recorded_at_utc).label("latest_recorded_at_utc"),
            )
            .group_by(gate_decision.c.gate_id)
            .subquery()
        )
        statement = (
            select(gate_decision)
            .join(
                latest,
                and_(
                    gate_decision.c.gate_id == latest.c.gate_id,
                    gate_decision.c.recorded_at_utc == latest.c.latest_recorded_at_utc,
                ),
            )
            .order_by(gate_decision.c.gate_id)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(statement).mappings().all()
        return [GateDecision(**dict(row)) for row in rows]
