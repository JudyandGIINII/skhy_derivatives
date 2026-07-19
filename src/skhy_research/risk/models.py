"""FR-14 리스크 엔진의 입력·정책 계약.

리스크 판정 결과는 공통 도메인 타입인 :class:`RiskDecision`을 사용하고, 이 모듈은
판정 시점에만 필요한 계좌·시장·비용 상태를 정의한다.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skhy_research.domain.enums import ConversionStatusValue
from skhy_research.domain.market import EpochNanos, NonNegativeDecimal


class StrategyRiskClass(StrEnum):
    H1 = "H1"
    H2 = "H2"
    H3 = "H3"


class MarketRiskState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    HALTED = "HALTED"
    VI = "VI"
    PRICE_LIMIT = "PRICE_LIMIT"
    UNKNOWN = "UNKNOWN"


class RiskReasonCode(StrEnum):
    """감사 로그와 테스트에서 고정해 사용하는 리스크 사유코드."""

    WITHIN_LIMITS = "WITHIN_LIMITS"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    DUPLICATE_LEG_ID = "DUPLICATE_LEG_ID"
    INVALID_ACCOUNT_EQUITY = "INVALID_ACCOUNT_EQUITY"
    CRITICAL_PROVIDER_DISCONNECTED = "CRITICAL_PROVIDER_DISCONNECTED"
    CRITICAL_PROVIDER_DELAYED = "CRITICAL_PROVIDER_DELAYED"
    PROVIDER_DIVERGENCE = "PROVIDER_DIVERGENCE"
    CLOCK_UNSYNCHRONIZED = "CLOCK_UNSYNCHRONIZED"
    LEG_STATE_MISSING = "LEG_STATE_MISSING"
    QUOTE_MISSING = "QUOTE_MISSING"
    QUOTE_FROM_FUTURE = "QUOTE_FROM_FUTURE"
    STALE_QUOTE = "STALE_QUOTE"
    MARKET_CLOSED = "MARKET_CLOSED"
    MARKET_HALTED = "MARKET_HALTED"
    MARKET_VI = "MARKET_VI"
    MARKET_PRICE_LIMIT = "MARKET_PRICE_LIMIT"
    MARKET_STATE_UNKNOWN = "MARKET_STATE_UNKNOWN"
    CONVERSION_UNAVAILABLE = "CONVERSION_UNAVAILABLE"
    BORROW_UNAVAILABLE = "BORROW_UNAVAILABLE"
    BORROW_COST_MISSING = "BORROW_COST_MISSING"
    BORROW_COST_EXPIRED = "BORROW_COST_EXPIRED"
    HEDGE_FAILURE = "HEDGE_FAILURE"
    UNHEDGED_LEG_TIMEOUT = "UNHEDGED_LEG_TIMEOUT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    CUMULATIVE_MDD_LIMIT = "CUMULATIVE_MDD_LIMIT"
    EXPECTED_COST_NOT_BELOW_GROSS = "EXPECTED_COST_NOT_BELOW_GROSS"
    STRATEGY_VERSION_MISMATCH = "STRATEGY_VERSION_MISMATCH"
    CONFIG_VERSION_MISMATCH = "CONFIG_VERSION_MISMATCH"
    DATA_SCHEMA_VERSION_MISMATCH = "DATA_SCHEMA_VERSION_MISMATCH"
    STOP_DISTANCE_MISSING = "STOP_DISTANCE_MISSING"
    LIQUIDITY_MISSING = "LIQUIDITY_MISSING"
    RISK_BUDGET_EXHAUSTED = "RISK_BUDGET_EXHAUSTED"
    MAX_RISK_PER_TRADE = "MAX_RISK_PER_TRADE"
    ORDER_RISK_BUDGET = "ORDER_RISK_BUDGET"
    LIQUIDITY_LIMIT = "LIQUIDITY_LIMIT"
    MINIMUM_LOT_ROUNDING = "MINIMUM_LOT_ROUNDING"
    NO_EXECUTABLE_QUANTITY = "NO_EXECUTABLE_QUANTITY"


class RiskPolicy(BaseModel):
    """PRD 11.2 한도. 비율은 백분율이 아니라 0~1 사이 fraction으로 저장한다."""

    model_config = ConfigDict(frozen=True)

    policy_version: str = "prd-11.2-v1"
    max_risk_per_trade_fraction: Decimal = Decimal("0.0025")
    max_daily_loss_fraction: Decimal = Decimal("0.01")
    max_cumulative_mdd_fraction: Decimal = Decimal("0.05")
    h1_quote_max_age_seconds: Decimal = Decimal("2")
    h2h3_quote_max_age_seconds: Decimal = Decimal("5")
    leg_timeout_seconds: Decimal = Decimal("5")

    @model_validator(mode="after")
    def _positive_limits(self) -> RiskPolicy:
        fractions = (
            self.max_risk_per_trade_fraction,
            self.max_daily_loss_fraction,
            self.max_cumulative_mdd_fraction,
        )
        if any(value <= 0 or value >= 1 for value in fractions):
            raise ValueError("리스크 비율 한도는 0보다 크고 1보다 작아야 한다")
        ages = (
            self.h1_quote_max_age_seconds,
            self.h2h3_quote_max_age_seconds,
            self.leg_timeout_seconds,
        )
        if any(value <= 0 for value in ages):
            raise ValueError("호가 나이와 leg timeout은 양수여야 한다")
        return self


class LegRiskState(BaseModel):
    """OrderIntent 한 다리의 판정 시점 시장·사이징 상태."""

    model_config = ConfigDict(frozen=True)

    quote_as_of_utc: EpochNanos | None
    stop_distance: NonNegativeDecimal | None
    available_quantity: NonNegativeDecimal | None
    minimum_trade_unit: Decimal = Decimal("1")
    market_state: MarketRiskState = MarketRiskState.OPEN
    requires_borrow: bool = False
    borrow_available: bool = True
    borrow_cost_expires_at_utc: EpochNanos | None = None

    @model_validator(mode="after")
    def _minimum_trade_unit_is_positive(self) -> LegRiskState:
        if self.minimum_trade_unit <= 0:
            raise ValueError("minimum_trade_unit은 양수여야 한다")
        return self


class RiskEvaluationContext(BaseModel):
    """한 주문을 평가하는 시점의 계좌·운영 상태 snapshot."""

    model_config = ConfigDict(frozen=True)

    now_utc: EpochNanos
    strategy_class: StrategyRiskClass
    account_equity: NonNegativeDecimal
    current_equity: NonNegativeDecimal
    high_water_mark_equity: NonNegativeDecimal
    daily_pnl: Decimal
    expected_gross_return: NonNegativeDecimal
    expected_cost: NonNegativeDecimal
    leg_states: dict[str, LegRiskState] = Field(default_factory=dict)
    critical_providers_connected: bool = True
    critical_providers_delayed: bool = False
    provider_divergence: bool = False
    clock_synchronized: bool = True
    conversion_status: ConversionStatusValue | None = None
    hedge_failed: bool = False
    unhedged_since_utc: EpochNanos | None = None
    strategy_version_approved: bool = True
    config_version_approved: bool = True
    data_schema_version_approved: bool = True
