"""sanitized fixture 기반 공급자 어댑터의 공통 지원 로직 (P0-07).

실제 네트워크를 호출하지 않고 미리 정제된(sanitized) payload를 반환하며,
인증 실패·rate limit·timeout·schema drift를 시나리오로 주입할 수 있다.
API key는 `SecretProvider`를 통해서만 조회하고(P0-03 마스킹 레지스트리에
자동 등록됨), 예외 메시지에 원문 값을 포함하지 않는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from skhy_research.ports.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderSchemaDriftError,
    ProviderTimeoutError,
)
from skhy_research.ports.secrets import SecretProvider


def compute_schema_fingerprint(payload: dict[str, Any]) -> str:
    """필드명:타입 조합의 정렬된 목록에 대한 sha256. 필드 추가·삭제·타입 변경을 감지한다."""
    parts = sorted(f"{key}:{type(value).__name__}" for key, value in payload.items())
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FixtureScenario:
    """단일 fixture 호출의 결과 시나리오. 정확히 하나의 결과만 발생해야 한다."""

    payload: Any = None
    auth_error: bool = False
    rate_limited: bool = False
    retry_after_seconds: float = 1.0
    timed_out: bool = False
    expected_schema_fingerprint: str | None = None  # None이면 drift 검사 생략


class FixtureCallGateway:
    """API key 주입·오류 시나리오·schema drift 검사를 공통 처리하는 헬퍼.

    각 fixture provider는 메서드 안에서 이 게이트웨이를 통해 시나리오를 평가한다.
    """

    def __init__(
        self,
        provider_name: str,
        secret_provider: SecretProvider | None = None,
        api_key_env: str | None = None,
        require_auth: bool = False,
    ) -> None:
        self._provider_name = provider_name
        self._secret_provider = secret_provider
        self._api_key_env = api_key_env
        self._require_auth = require_auth

    def resolve(self, scenario: FixtureScenario) -> Any:
        if self._require_auth:
            self._authenticate()
        if scenario.auth_error:
            raise ProviderAuthenticationError(self._provider_name)
        if scenario.rate_limited:
            raise ProviderRateLimitError(self._provider_name, scenario.retry_after_seconds)
        if scenario.timed_out:
            raise ProviderTimeoutError(self._provider_name)
        if scenario.expected_schema_fingerprint is not None and isinstance(scenario.payload, dict):
            actual = compute_schema_fingerprint(scenario.payload)
            if actual != scenario.expected_schema_fingerprint:
                raise ProviderSchemaDriftError(
                    self._provider_name, scenario.expected_schema_fingerprint, actual
                )
        return scenario.payload

    def _authenticate(self) -> None:
        if self._secret_provider is None or self._api_key_env is None:
            raise ProviderAuthenticationError(self._provider_name)
        key = self._secret_provider.get_secret(self._api_key_env)
        if not key:
            raise ProviderAuthenticationError(self._provider_name)
