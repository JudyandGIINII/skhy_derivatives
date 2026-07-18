"""공급자 포트 공통 예외."""

from __future__ import annotations

from skhy_research.domain.provider_capability import ProviderCapability


class UnsupportedCapabilityError(RuntimeError):
    """공급자가 지원하지 않는 기능 요청. 빈 데이터로 위장하지 않는다 (PRD 7.4)."""

    def __init__(self, provider_name: str, capability: ProviderCapability) -> None:
        super().__init__(f"{provider_name}는 {capability.value}를 지원하지 않는다")
        self.provider_name = provider_name
        self.capability = capability


class ProviderError(RuntimeError):
    """공급자 계약 오류의 공통 기저. PRD 14.1 계약 테스트가 다루는 오류 taxonomy."""

    def __init__(self, provider_name: str, message: str) -> None:
        super().__init__(f"[{provider_name}] {message}")
        self.provider_name = provider_name


class ProviderAuthenticationError(ProviderError):
    def __init__(self, provider_name: str) -> None:
        super().__init__(provider_name, "인증 실패 또는 token 만료")


class ProviderRateLimitError(ProviderError):
    def __init__(self, provider_name: str, retry_after_seconds: float) -> None:
        super().__init__(provider_name, f"호출 제한 도달, {retry_after_seconds}초 후 재시도")
        self.retry_after_seconds = retry_after_seconds


class ProviderTimeoutError(ProviderError):
    def __init__(self, provider_name: str) -> None:
        super().__init__(provider_name, "요청 timeout")


class ProviderSchemaDriftError(ProviderError):
    """API 응답 스키마의 필드 추가·삭제·타입 변경을 감지했을 때 (PRD 14.1)."""

    def __init__(self, provider_name: str, expected_fingerprint: str, actual_fingerprint: str) -> None:
        super().__init__(
            provider_name,
            f"schema fingerprint 불일치: expected={expected_fingerprint} actual={actual_fingerprint}",
        )
        self.expected_fingerprint = expected_fingerprint
        self.actual_fingerprint = actual_fingerprint
