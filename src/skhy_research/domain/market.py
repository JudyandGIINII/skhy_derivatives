"""RecordEnvelope와 시장 데이터 핵심 타입 (PRD 8.1, 8.2).

모든 금액·가격·수량은 `Decimal`로 표현한다(PRD 8.1: "이진 부동소수점 대신
고정소수점 또는 Decimal을 사용한다"). 시각은 UTC epoch nanoseconds(`int`)이며
화면 표시용 ISO 8601 변환은 이 계층의 책임이 아니다(adapters/reporting에서 수행).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    MarketDataFeedMode,
    OrderSide,
    QualityFlag,
    Session,
    Venue,
)


def _validate_epoch_nanos(value: int) -> int:
    if value < 0:
        raise ValueError("UTC epoch nanoseconds는 음수일 수 없다")
    return value


EpochNanos = Annotated[int, AfterValidator(_validate_epoch_nanos)]


def _validate_non_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("가격·수량은 음수일 수 없다")
    return value


NonNegativeDecimal = Annotated[Decimal, AfterValidator(_validate_non_negative)]


class RecordEnvelope(BaseModel):
    """PRD 8.1 공통 봉투. 값이 적용되지 않으면 필드를 생략하지 않고 null+사유를 남긴다."""

    model_config = ConfigDict(frozen=True)

    source: str
    venue: Venue
    symbol: str
    event_time_utc: EpochNanos
    received_time_utc: EpochNanos
    currency: Currency | None
    currency_na_reason: str | None = None
    session: Session
    is_delayed: bool
    adjustment_status: AdjustmentStatus
    quality_flag: list[QualityFlag] = Field(default_factory=list)

    @model_validator(mode="after")
    def _currency_null_requires_reason(self) -> RecordEnvelope:
        if self.currency is None and not self.currency_na_reason:
            raise ValueError("currency가 null이면 currency_na_reason을 함께 저장해야 한다")
        return self

    @model_validator(mode="after")
    def _received_not_before_event(self) -> RecordEnvelope:
        # 동일 시각(리플레이 등)은 허용하되, 수신이 이벤트보다 앞서는 것은 시계 오류다.
        if self.received_time_utc < self.event_time_utc:
            raise ValueError("received_time_utc가 event_time_utc보다 이를 수 없다")
        return self


class MarketQuote(RecordEnvelope):
    """PRD 8.2 MarketQuote."""

    instrument_id: str
    bid_price: NonNegativeDecimal
    ask_price: NonNegativeDecimal
    bid_size: NonNegativeDecimal
    ask_size: NonNegativeDecimal

    # bid > ask(crossed quote)는 구조적 오류가 아니라 데이터 품질 이벤트다.
    # 이 타입은 값을 거부하지 않고 그대로 보존하며, 탐지·QualityFlag 부여는
    # P0-09 정규화·품질 파이프라인의 책임이다.


class ObservationTimeSource(StrEnum):
    PROVIDER_DATE_TIME = "PROVIDER_DATE_TIME"
    PROVIDER_TIME_WITH_BATCH_DATE = "PROVIDER_TIME_WITH_BATCH_DATE"
    PROVIDER_TIMESTAMP = "PROVIDER_TIMESTAMP"


class PublicationTimeSource(StrEnum):
    CLIENT_RECEIVED_AT = "CLIENT_RECEIVED_AT"


class IndicativeValueKind(StrEnum):
    NAV = "NAV"
    IV = "IV"


class MarketPriceSnapshot(RecordEnvelope):
    """특정 시점에 REST로 조회한 최종가 스냅샷.

    `MarketQuote`는 양방향 호가를 요구하므로, 현재가만 제공하는 KIS/Toss
    endpoint에 없는 bid/ask를 만들지 않기 위해 별도 계약으로 보존한다.
    `event_time_utc`는 공급자가 제공한 관측시각이고, `published_time_utc`는
    신호 생성자가 값을 이용할 수 있게 된 시각이다.
    """

    record_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    last_price: NonNegativeDecimal
    last_size: NonNegativeDecimal | None = None
    published_time_utc: EpochNanos
    observation_time_source: ObservationTimeSource
    publication_time_source: PublicationTimeSource
    feed_mode: MarketDataFeedMode
    indicative_value: NonNegativeDecimal | None = None
    indicative_value_kind: IndicativeValueKind | None = None
    indicative_value_observed_at_utc: EpochNanos | None = None

    @model_validator(mode="after")
    def _publication_is_lookahead_safe(self) -> MarketPriceSnapshot:
        if not (self.event_time_utc <= self.published_time_utc <= self.received_time_utc):
            raise ValueError(
                "event_time_utc <= published_time_utc <= received_time_utc여야 한다"
            )
        return self

    @model_validator(mode="after")
    def _indicative_value_has_kind_and_time(self) -> MarketPriceSnapshot:
        metadata = (self.indicative_value_kind, self.indicative_value_observed_at_utc)
        if self.indicative_value is None and any(value is not None for value in metadata):
            raise ValueError("indicative_value가 없으면 종류·관측시각도 없어야 한다")
        if self.indicative_value is not None and any(value is None for value in metadata):
            raise ValueError("indicative_value가 있으면 종류·관측시각이 필요하다")
        if (
            self.indicative_value_observed_at_utc is not None
            and self.indicative_value_observed_at_utc > self.published_time_utc
        ):
            raise ValueError("indicative value 관측시각은 게시·수신시각보다 늦을 수 없다")
        return self


class Trade(RecordEnvelope):
    """PRD 8.2 Trade. 매수·매도 방향은 제공될 때만 채운다."""

    instrument_id: str
    price: NonNegativeDecimal
    quantity: NonNegativeDecimal
    side: OrderSide | None = None


class BarConstructionMethod(BaseModel):
    """Bar 생성 기준. 서로 다른 공급자의 bar를 조용히 이어 붙이지 않기 위한 출처 기록."""

    model_config = ConfigDict(frozen=True)

    method: str  # 예: VENDOR_PROVIDED, AGGREGATED_FROM_TICKS, AGGREGATED_FROM_QUOTES
    source_segment: str  # 이 구간의 실제 데이터 출처(예: "KRX:2026-01-01..2026-03-31")


class Bar(RecordEnvelope):
    """PRD 8.2 Bar. period는 "1s"/"5s"/"1m"/"5m"/"1d" 등 명시적 주기 문자열이다."""

    instrument_id: str
    period: str
    open: NonNegativeDecimal
    high: NonNegativeDecimal
    low: NonNegativeDecimal
    close: NonNegativeDecimal
    volume: NonNegativeDecimal
    turnover: NonNegativeDecimal | None = None
    is_adjusted: bool
    construction: BarConstructionMethod
    bar_close_time_utc: EpochNanos

    @model_validator(mode="after")
    def _high_low_bounds(self) -> Bar:
        if self.high < self.low:
            raise ValueError("high는 low보다 작을 수 없다")
        if not (self.low <= self.open <= self.high):
            raise ValueError("open은 [low, high] 범위 안에 있어야 한다")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close는 [low, high] 범위 안에 있어야 한다")
        return self


class FXQuote(RecordEnvelope):
    """PRD 8.2 FXQuote. 방향은 항상 1 USD당 KRW로 고정한다 (v1은 USD/KRW만 지원)."""

    pair: str = Field(default="USD/KRW", frozen=True)
    bid: NonNegativeDecimal
    ask: NonNegativeDecimal
    rate_kind: str  # DAILY_REFERENCE | EXECUTION

    @model_validator(mode="after")
    def _pair_is_usdkrw(self) -> FXQuote:
        if self.pair != "USD/KRW":
            raise ValueError("v1은 USD/KRW 방향만 지원한다 (PRD 8.2)")
        return self

    @model_validator(mode="after")
    def _rate_kind_is_known(self) -> FXQuote:
        if self.rate_kind not in {"DAILY_REFERENCE", "EXECUTION"}:
            raise ValueError("rate_kind는 DAILY_REFERENCE 또는 EXECUTION이어야 한다")
        return self
