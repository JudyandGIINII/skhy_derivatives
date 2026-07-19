"""확정된 gate 결정의 기계용 seed 소스.

`docs/decisions/gates/*.md`는 검토자를 위한 사람용 서술이고, 이 모듈이 PostgreSQL
`gate_decision` journal에 넣을 확정(CONFIRMED) 결정의 타입드 소스다. Markdown을
파싱하지 않는다. 각 결정의 `evidence_checksum`은 대응 evidence 파일의 SHA-256과
일치해야 하며 `test_gate_decision_seed`가 이를 강제한다.

seed는 `application.gate_registry_loader.load_gate_registry`가 읽는 것과 동일한
결정을 넣는다. 즉 여기 CONFIRMED로 넣은 gate만 런타임에서 차단이 풀린다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from skhy_research.domain.gate import GateDecision, GateStatus


def _nanos(iso_utc: str) -> int:
    parsed = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1_000_000_000)


@dataclass(frozen=True)
class _ConfirmedGate:
    gate_id: str
    evidence_url: str
    evidence_checksum: str
    evidence_relpath: str  # checksum 정합성 검증 대상 (repo 상대경로)
    responsible_provider: str
    conclusion: str
    confirmed_at_iso: str
    valid_until_iso: str


# 확정 값은 docs/decisions/gates/{G-02,G-04,G-06}.md의 결정 기록과 일치한다.
# G-06은 시스템 소유자의 개인용 사용 결정이며 evidence_url은 gate가 실제로 통제하는
# 주 데이터 공급자(KRX) 참조를 둔다.
_CONFIRMED_GATES: tuple[_ConfirmedGate, ...] = (
    _ConfirmedGate(
        gate_id="G-02",
        evidence_url="https://apiportal.koreainvestment.com/apiservice-category",
        evidence_checksum="c11c6779bbeffd02efb6b900602fdfa15f68c6db57b39632b9ae0ed109c82b91",
        evidence_relpath="docs/decisions/gates/evidence/G-02-capability-probe.json",
        responsible_provider="한국투자증권(KIS), 토스증권",
        conclusion="KRX/KIS/Toss read-only capability probe 실측 확인 (조회 전용)",
        confirmed_at_iso="2026-07-18T09:57:26Z",
        valid_until_iso="2026-10-16T09:57:26Z",
    ),
    _ConfirmedGate(
        gate_id="G-04",
        evidence_url="https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd",
        evidence_checksum="5e962fab2172e3d55712f670c1706487e6568008187f2e77a55a573cc14285d1",
        evidence_relpath="docs/decisions/gates/evidence/G-04-h1-required-fields.md",
        responsible_provider="KRX Open API",
        conclusion="무료 KRX 일별 H1 universe·listed-notional proxy·가용성·lineage 계약 확인",
        confirmed_at_iso="2026-07-18T12:26:33Z",
        valid_until_iso="2026-10-16T12:26:33Z",
    ),
    _ConfirmedGate(
        gate_id="G-06",
        evidence_url="https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd",
        evidence_checksum="0b2270e45fda0f40c8f9c4970818a41296eafe884bf8d21f0a33cae795e3504a",
        evidence_relpath="docs/decisions/gates/evidence/G-06-data-terms.md",
        responsible_provider="시스템 소유자(개인)",
        conclusion="소유자 개인용 단일 호스트 연구 목적 수집·로컬 저장 허용 결정 (위험 인수)",
        confirmed_at_iso="2026-07-18T19:06:27+09:00",
        valid_until_iso="2027-07-18T19:06:27+09:00",
    ),
)


def confirmed_seed_decisions(recorded_at_utc: int) -> list[GateDecision]:
    """확정 gate 결정을 지정한 기록시각으로 GateDecision 리스트로 만든다."""
    decisions: list[GateDecision] = []
    for gate in _CONFIRMED_GATES:
        decisions.append(
            GateDecision(
                gate_id=gate.gate_id,
                status=GateStatus.CONFIRMED,
                evidence_url=gate.evidence_url,
                evidence_checksum=gate.evidence_checksum,
                responsible_provider=gate.responsible_provider,
                conclusion=gate.conclusion,
                confirmed_at_utc=_nanos(gate.confirmed_at_iso),
                valid_until_utc=_nanos(gate.valid_until_iso),
                recorded_at_utc=recorded_at_utc,
            )
        )
    return decisions


def confirmed_seed_evidence_relpaths() -> dict[str, str]:
    """gate_id -> evidence 파일 상대경로. checksum 정합성 검증용."""
    return {gate.gate_id: gate.evidence_relpath for gate in _CONFIRMED_GATES}


def confirmed_seed_checksums() -> dict[str, str]:
    """gate_id -> author된 evidence checksum."""
    return {gate.gate_id: gate.evidence_checksum for gate in _CONFIRMED_GATES}


class GateDecisionStore(Protocol):
    def save_decision(self, decision: GateDecision) -> None: ...

    def load_all_decisions(self) -> list[GateDecision]: ...


@dataclass(frozen=True)
class GateSeedOutcome:
    gate_id: str
    action: str  # "inserted" | "already-current"


def _same_confirmed_content(current: GateDecision, target: GateDecision) -> bool:
    """recorded_at_utc를 제외한 결정 내용이 같은지 비교 (멱등 판정)."""
    fields = (
        "status",
        "evidence_url",
        "evidence_checksum",
        "responsible_provider",
        "conclusion",
        "confirmed_at_utc",
        "valid_until_utc",
    )
    return all(getattr(current, name) == getattr(target, name) for name in fields)


def seed_confirmed_gate_decisions(
    store: GateDecisionStore, *, recorded_at_utc: int
) -> list[GateSeedOutcome]:
    """확정 gate 결정을 append-only journal에 멱등 저장한다.

    각 gate의 최신 결정이 이미 동일 내용이면 새 행을 추가하지 않는다(반복 실행 안전).
    """
    existing = {decision.gate_id: decision for decision in store.load_all_decisions()}
    outcomes: list[GateSeedOutcome] = []
    for decision in confirmed_seed_decisions(recorded_at_utc):
        current = existing.get(decision.gate_id)
        if current is not None and _same_confirmed_content(current, decision):
            outcomes.append(GateSeedOutcome(decision.gate_id, "already-current"))
            continue
        store.save_decision(decision)
        outcomes.append(GateSeedOutcome(decision.gate_id, "inserted"))
    return outcomes
