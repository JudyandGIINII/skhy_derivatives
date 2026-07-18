"""원 H1 observable-flow 입력 계약 (PRD 9.1, G-03).

결측값과 실제 관측값 0을 구분한다. 값이 없으면 수치 0으로 바꾸지 않고
``ObservableFlowAdjustment.value=None``과 필드별 결측 사유를 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from skhy_research.domain.enums import ReplicationType


class ObservableFlowField(StrEnum):
    CLOSE_AUCTION_IMBALANCE = "close_auction_imbalance"
    PROGRAM_NET_BUY = "program_net_buy"
    NET_CREATION_REDEMPTION = "net_creation_redemption"
    REPLICATION_TYPE = "replication_type"
    REPLICATION_FLOW_MULTIPLIER = "replication_flow_multiplier"


@dataclass(frozen=True)
class FlowObservation:
    """시점·lineage가 있는 하나의 flow 관측값. 실제 0은 유효한 관측이다."""

    value: Decimal | None
    available_at_utc: int | None
    input_record_id: str | None
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            if not self.missing_reason:
                raise ValueError("결측 flow 관측에는 missing_reason이 필요하다")
            return
        if self.available_at_utc is None or self.available_at_utc < 0:
            raise ValueError("관측된 flow에는 유효한 available_at_utc가 필요하다")
        if self.input_record_id is None or not self.input_record_id.strip():
            raise ValueError("관측된 flow에는 input_record_id가 필요하다")
        if self.missing_reason is not None:
            raise ValueError("관측된 flow에는 missing_reason을 둘 수 없다")


@dataclass(frozen=True)
class ReplicationFlowEvidence:
    """복제방식을 설정·환매 flow에 반영하기 위한 명시적 근거."""

    replication_type: ReplicationType | None
    creation_flow_multiplier: Decimal | None
    available_at_utc: int | None
    input_record_id: str | None
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        complete = self.replication_type is not None and self.creation_flow_multiplier is not None
        if not complete:
            if not self.missing_reason:
                raise ValueError("결측 복제방식 근거에는 missing_reason이 필요하다")
            return
        if self.available_at_utc is None or self.available_at_utc < 0:
            raise ValueError("복제방식 근거에는 유효한 available_at_utc가 필요하다")
        if self.input_record_id is None or not self.input_record_id.strip():
            raise ValueError("복제방식 근거에는 input_record_id가 필요하다")
        if self.missing_reason is not None:
            raise ValueError("완전한 복제방식 근거에는 missing_reason을 둘 수 없다")


@dataclass(frozen=True)
class ObservableFlowInput:
    close_auction_imbalance_notional: FlowObservation
    program_net_buy_notional: FlowObservation
    net_creation_redemption_notional: FlowObservation
    replication: ReplicationFlowEvidence


@dataclass(frozen=True)
class ObservableFlowAdjustment:
    value: Decimal | None
    missing_fields: tuple[ObservableFlowField, ...]
    missing_reasons: tuple[str, ...]
    input_record_ids: tuple[str, ...]
    replication_adjusted_creation_notional: Decimal | None


def calculate_observable_flow_adjustment(
    flow: ObservableFlowInput, *, decision_time_utc: int
) -> ObservableFlowAdjustment:
    """세 수치 flow와 명시적 복제 multiplier를 결합한다.

    어느 필드든 결측이면 부분 합계를 full H1 값으로 반환하지 않는다. 사용 가능한
    근거의 lineage만 보존하고 ``value``는 ``None``으로 둔다.
    """

    missing: list[ObservableFlowField] = []
    reasons: list[str] = []
    lineage: list[str] = []
    observations = (
        (
            ObservableFlowField.CLOSE_AUCTION_IMBALANCE,
            flow.close_auction_imbalance_notional,
        ),
        (ObservableFlowField.PROGRAM_NET_BUY, flow.program_net_buy_notional),
        (
            ObservableFlowField.NET_CREATION_REDEMPTION,
            flow.net_creation_redemption_notional,
        ),
    )
    for field, observation in observations:
        if observation.value is None:
            missing.append(field)
            reasons.append(f"{field.value}:{observation.missing_reason}")
            continue
        assert observation.available_at_utc is not None
        assert observation.input_record_id is not None
        if observation.available_at_utc > decision_time_utc:
            raise ValueError(f"{field.value}가 15:10 decision 이후에 가용해졌다")
        _append_unique(lineage, observation.input_record_id)

    replication = flow.replication
    if replication.replication_type is None:
        missing.append(ObservableFlowField.REPLICATION_TYPE)
        reasons.append(
            f"{ObservableFlowField.REPLICATION_TYPE.value}:{replication.missing_reason}"
        )
    if replication.creation_flow_multiplier is None:
        missing.append(ObservableFlowField.REPLICATION_FLOW_MULTIPLIER)
        reasons.append(
            f"{ObservableFlowField.REPLICATION_FLOW_MULTIPLIER.value}:"
            f"{replication.missing_reason}"
        )
    if (
        replication.replication_type is not None
        and replication.creation_flow_multiplier is not None
    ):
        assert replication.available_at_utc is not None
        assert replication.input_record_id is not None
        if replication.available_at_utc > decision_time_utc:
            raise ValueError("replication evidence가 15:10 decision 이후에 가용해졌다")
        _append_unique(lineage, replication.input_record_id)

    if missing:
        return ObservableFlowAdjustment(
            value=None,
            missing_fields=tuple(missing),
            missing_reasons=tuple(reasons),
            input_record_ids=tuple(lineage),
            replication_adjusted_creation_notional=None,
        )

    auction = flow.close_auction_imbalance_notional.value
    program = flow.program_net_buy_notional.value
    creation = flow.net_creation_redemption_notional.value
    multiplier = replication.creation_flow_multiplier
    assert auction is not None
    assert program is not None
    assert creation is not None
    assert multiplier is not None
    replication_adjusted_creation = creation * multiplier
    return ObservableFlowAdjustment(
        value=auction + program + replication_adjusted_creation,
        missing_fields=(),
        missing_reasons=(),
        input_record_ids=tuple(lineage),
        replication_adjusted_creation_notional=replication_adjusted_creation,
    )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
