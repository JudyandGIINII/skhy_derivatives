"""공급자 capability·라이선스·지연 catalog 공통 계약 (PRD 7.4).

모든 공급자 구현은 이 catalog로 capabilities, 호출·구독 한도, 지연 특성,
저장·재배포 조건, 마지막 확인일을 노출해야 한다. 지원하지 않는 기능은
빈 데이터로 위장하지 않고 명시적 오류(`UnsupportedCapabilityError`)를 낸다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from skhy_research.domain.market import EpochNanos


class ProviderCapability(StrEnum):
    QUOTE_SNAPSHOT = "QUOTE_SNAPSHOT"
    QUOTE_STREAM = "QUOTE_STREAM"
    TRADE_STREAM = "TRADE_STREAM"
    EXPECTED_CLOSING_PRICE = "EXPECTED_CLOSING_PRICE"
    INSTRUMENT_MASTER = "INSTRUMENT_MASTER"
    CORPORATE_ACTIONS = "CORPORATE_ACTIONS"
    FUND_SNAPSHOT = "FUND_SNAPSHOT"
    ADR_RATIO_CONVERSION_STATUS = "ADR_RATIO_CONVERSION_STATUS"
    BORROW_QUOTE = "BORROW_QUOTE"
    HISTORICAL_BARS = "HISTORICAL_BARS"
    HISTORICAL_STATISTICS = "HISTORICAL_STATISTICS"
    INSTRUMENT_LOOKUP = "INSTRUMENT_LOOKUP"
    ACCOUNT_SNAPSHOT = "ACCOUNT_SNAPSHOT"
    ORDER_SUBMIT = "ORDER_SUBMIT"
    ORDER_CANCEL = "ORDER_CANCEL"
    FILL_EVENTS = "FILL_EVENTS"


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class ProviderLicenseTerms(BaseModel):
    """raw 수집 시점에 동결해 보존할 공급자 이용조건 snapshot."""

    model_config = ConfigDict(frozen=True)

    license_terms_url: str = Field(min_length=1)
    storage_redistribution_allowed: bool


class ProviderCatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_name: str
    port_type: str  # market_data|reference_data|historical_data|broker
    catalog_version: str = Field(min_length=1)
    capabilities: frozenset[ProviderCapability]
    call_rate_limit_per_min: int | None = None
    subscription_limit: int | None = None
    expected_latency_ms: float | None = None
    measured_latency_ms: float | None = None
    license_terms_url: str = Field(min_length=1)
    storage_redistribution_allowed: bool
    last_verified_at_utc: EpochNanos
    health_status: HealthStatus = HealthStatus.UNKNOWN

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    def license_terms_snapshot(self) -> ProviderLicenseTerms:
        """현재 catalog에 확인된 이용조건을 raw 계보용 불변 값으로 복사한다."""
        return ProviderLicenseTerms(
            license_terms_url=self.license_terms_url,
            storage_redistribution_allowed=self.storage_redistribution_allowed,
        )


class ConnectionHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_connected: bool
    measured_latency_ms: float | None = None
    last_event_at_utc: EpochNanos | None = None
    reconnect_count: int = 0


class ReadOnlyProbeEvidence(BaseModel):
    """실 API probe가 반환하는 민감값 없는 최소 증거."""

    model_config = ConfigDict(frozen=True)

    provider_name: str
    endpoint: str
    record_count: int
    observed_fields: tuple[str, ...]
    measured_latency_ms: float
