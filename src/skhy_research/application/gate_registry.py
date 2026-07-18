"""G-01~G-08 gate 상태 관리 (P0-11, PRD 19장, `implementation_plan.md` 7장).

미확인·만료·거부 상태는 항상 `blocks()=True`다 ("미확인 상태의 기본 동작은
BLOCK이다"). CONFIRMED 기록에는 evidence_url을 강제해 언론·SNS 보도만으로
상태를 바꾸는 것을 최소한 구조적으로 어렵게 한다(완전한 방지는 절차의
영역이며 코드로 강제할 수 없다).
"""

from __future__ import annotations

from skhy_research.domain.gate import GateDecision, GateDefinition, GateStatus

GATE_DEFINITIONS: dict[str, GateDefinition] = {
    "G-01": GateDefinition(
        gate_id="G-01",
        question="Citi/KSD 및 실제 브로커의 보통주↔ADR 전환 개시·방향·최소수량·비용·처리기간",
        default_action_if_unresolved="ConversionStatus=UNKNOWN, H2 신규 진입 차단",
    ),
    "G-02": GateDefinition(
        gate_id="G-02",
        question="KIS·Toss의 계정별 실시간 시장·세션·필드 범위, 호출·구독 한도, token 수명",
        default_action_if_unresolved="지원 기능으로 가정하지 않음(UNSUPPORTED_CAPABILITY)",
    ),
    "G-03": GateDefinition(
        gate_id="G-03",
        question="종가 예상체결 불균형·프로그램매매·호가 깊이의 수집 가능성 및 비용",
        default_action_if_unresolved="H1 축소모델로 분리하고 품질 경고, 완전모델과 성능 미합산",
    ),
    "G-04": GateDefinition(
        gate_id="G-04",
        question="국내 단일종목 레버리지 상품의 실제 종목 목록, PCF·AUM/NAV 공개시각, 복제방식",
        default_action_if_unresolved="동적 발견·공개시각·구조 불명확 상품을 H1에서 제외",
    ),
    "G-05": GateDefinition(
        gate_id="G-05",
        question="SKHY 대차 가능수량·금리·리콜 조건과 대체 헤지의 거래 가능성",
        default_action_if_unresolved="H2 신규 페어 차단, 단독 본주 롱 금지",
    ),
    "G-06": GateDefinition(
        gate_id="G-06",
        question="원천·정규화 데이터의 저장기간, 자동수집, 재배포 가능 범위",
        default_action_if_unresolved="로컬 최소 보관만 허용, 외부 배포 금지, 불명확 dataset 수집 중지",
    ),
    "G-07": GateDefinition(
        gate_id="G-07",
        question="공매도·ADR·해외주식·파생상품 관련 규제, 세금과 신고 의무",
        default_action_if_unresolved="보수적 비용 가정 또는 비실행 처리, 실거래 승격은 무조건 금지",
    ),
    "G-08": GateDefinition(
        gate_id="G-08",
        question="페이퍼 체결모델의 기본 주문 크기와 초기 모의자본",
        default_action_if_unresolved="절대수익보다 단위 위험·수익률로만 평가, 절대 PnL로 승격 금지",
    ),
}


class UnknownGateError(RuntimeError):
    pass


class InvalidGateDecisionError(RuntimeError):
    pass


class GateRegistry:
    def __init__(self) -> None:
        self._decisions: dict[str, GateDecision] = {}

    def definitions(self) -> dict[str, GateDefinition]:
        return dict(GATE_DEFINITIONS)

    def record_decision(self, decision: GateDecision) -> None:
        if decision.gate_id not in GATE_DEFINITIONS:
            raise UnknownGateError(f"알 수 없는 gate_id: {decision.gate_id}")
        if decision.status == GateStatus.CONFIRMED and not decision.evidence_url:
            raise InvalidGateDecisionError(
                f"{decision.gate_id}를 CONFIRMED로 기록하려면 evidence_url이 필요하다"
            )
        self._decisions[decision.gate_id] = decision

    def effective_status(self, gate_id: str, as_of_utc: int) -> GateStatus:
        if gate_id not in GATE_DEFINITIONS:
            raise UnknownGateError(f"알 수 없는 gate_id: {gate_id}")
        decision = self._decisions.get(gate_id)
        if decision is None:
            return GateStatus.UNKNOWN
        if (
            decision.status == GateStatus.CONFIRMED
            and decision.valid_until_utc is not None
            and as_of_utc >= decision.valid_until_utc
        ):
            return GateStatus.EXPIRED
        return decision.status

    def is_resolved(self, gate_id: str, as_of_utc: int) -> bool:
        return self.effective_status(gate_id, as_of_utc) == GateStatus.CONFIRMED

    def blocks(self, gate_id: str, as_of_utc: int) -> bool:
        """UNKNOWN/IN_REVIEW/REJECTED/EXPIRED는 모두 BLOCK이다 (PRD 19장 대원칙)."""
        return not self.is_resolved(gate_id, as_of_utc)
