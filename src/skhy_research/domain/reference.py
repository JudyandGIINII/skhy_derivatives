"""기준정보 타입: FundSnapshot, ConversionStatus, BorrowQuote (PRD 8.2, 5.2, 5.3)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import model_validator

from skhy_research.domain.enums import ConversionStatusValue, ReplicationType
from skhy_research.domain.market import EpochNanos, NonNegativeDecimal, RecordEnvelope


class FundSnapshot(RecordEnvelope):
    """레버리지·인버스 상품의 AUM/NAV/iNAV 스냅샷. `venue`/`currency`는 RecordEnvelope에서 상속."""

    fund_id: str
    leverage_beta: Decimal  # 예: 2, -1, -2
    aum: NonNegativeDecimal
    nav: NonNegativeDecimal
    inav: NonNegativeDecimal | None = None
    shares_outstanding: NonNegativeDecimal | None = None
    net_creation_estimate: Decimal | None = None
    net_creation_estimate_method: str | None = None
    replication_type: ReplicationType
    published_at: EpochNanos  # 실제 공개시각
    effective_at: EpochNanos  # 값의 기준시각

    @model_validator(mode="after")
    def _net_creation_needs_method(self) -> FundSnapshot:
        if self.net_creation_estimate is not None and not self.net_creation_estimate_method:
            raise ValueError("net_creation_estimate가 있으면 추정방법을 함께 기록해야 한다")
        return self


class ConversionStatus(RecordEnvelope):
    """PRD 5.2 전환 상태. 언론·SNS 단독 보도는 상태를 바꾸지 못한다(운영 규칙, 타입 레벨 강제 아님)."""

    status: ConversionStatusValue
    adr_ratio_common_to_adr: Decimal  # 경제적 등가비율, v1은 1:10
    min_quantity: NonNegativeDecimal | None = None
    fee_description: str | None = None
    estimated_settlement_days: int | None = None
    evidence_url: str
    confirmed_at_utc: EpochNanos

    @model_validator(mode="after")
    def _operational_requires_full_evidence(self) -> ConversionStatus:
        if self.status == ConversionStatusValue.OPERATIONAL:
            missing = [
                name
                for name, value in (
                    ("min_quantity", self.min_quantity),
                    ("fee_description", self.fee_description),
                    ("estimated_settlement_days", self.estimated_settlement_days),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    "OPERATIONAL 판정에는 min_quantity/fee_description/"
                    f"estimated_settlement_days가 모두 필요하다 (누락: {missing})"
                )
        return self


class BorrowQuote(RecordEnvelope):
    """PRD 8.2 BorrowQuote. 만료되면 H2 페어를 거래 가능 상태로 판정하지 않는다(리스크 엔진 책임)."""

    instrument_id: str
    available_quantity: NonNegativeDecimal
    annualized_rate_pct: NonNegativeDecimal
    recall_terms: str | None = None
    provider: str
    valid_until_utc: EpochNanos
