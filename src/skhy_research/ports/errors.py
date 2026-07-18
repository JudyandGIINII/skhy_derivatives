"""공급자 포트 공통 예외."""

from __future__ import annotations

from skhy_research.domain.provider_capability import ProviderCapability


class UnsupportedCapabilityError(RuntimeError):
    """공급자가 지원하지 않는 기능 요청. 빈 데이터로 위장하지 않는다 (PRD 7.4)."""

    def __init__(self, provider_name: str, capability: ProviderCapability) -> None:
        super().__init__(f"{provider_name}는 {capability.value}를 지원하지 않는다")
        self.provider_name = provider_name
        self.capability = capability
