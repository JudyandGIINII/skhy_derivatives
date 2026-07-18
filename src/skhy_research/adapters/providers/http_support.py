"""실 API 어댑터의 공통 HTTP 오류 변환.

응답 body나 요청 헤더를 예외 문구에 넣지 않아 API key·token이
로그와 테스트 출력에 노출되지 않게 한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import httpx

from skhy_research.ports.errors import (
    ProviderAccessDeniedError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from skhy_research.ports.secrets import SecretProvider

_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

# HTTP 403으로 응답하지만 실제로는 권한 거부가 아니라 호출 속도제한인 provider별
# 알려진 오류 코드. 예: KIS 접근토큰 발급 "EGW00133"(1분당 1회 제한).
_RATE_LIMIT_ERROR_CODES_ON_403 = frozenset({"EGW00133"})


def require_secret(secret_provider: SecretProvider, provider_name: str, name: str) -> str:
    value = secret_provider.get_secret(name)
    if not value:
        raise ProviderAuthenticationError(provider_name)
    return value


def request_json(
    provider_name: str,
    send: Callable[[], httpx.Response],
) -> dict[str, Any]:
    # pytest가 실패 frame의 호출 인자를 출력해도 send closure의 비밀값은 표시되지 않는다.
    __tracebackhide__ = True
    try:
        response = send()
    except httpx.TimeoutException:
        raise ProviderTimeoutError(provider_name) from None
    except httpx.RequestError:
        raise ProviderTransportError(provider_name) from None

    error_code = _extract_error_code(response)
    if response.status_code == 401:
        raise ProviderAuthenticationError(provider_name, error_code) from None
    if response.status_code == 403:
        if error_code in _RATE_LIMIT_ERROR_CODES_ON_403:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise ProviderRateLimitError(provider_name, retry_after) from None
        raise ProviderAccessDeniedError(provider_name, error_code) from None
    if response.status_code == 429:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        raise ProviderRateLimitError(provider_name, retry_after) from None
    if response.is_error:
        raise ProviderResponseError(
            provider_name,
            status_code=response.status_code,
            error_code=error_code,
        ) from None

    try:
        payload = response.json()
    except ValueError:
        raise ProviderResponseError(provider_name, status_code=response.status_code) from None
    if not isinstance(payload, dict):
        raise ProviderResponseError(provider_name, status_code=response.status_code)
    return payload


def _parse_retry_after(value: str | None) -> float:
    if value is None:
        return 60.0
    try:
        return max(0.0, float(value))
    except ValueError:
        return 60.0


def _extract_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    candidates: list[object] = [
        payload.get("msg_cd"),
        payload.get("error_code"),
        payload.get("resultCode"),
        payload.get("code"),
        payload.get("error"),
    ]
    nested_error = payload.get("error")
    if isinstance(nested_error, dict):
        candidates.insert(0, nested_error.get("code"))
    for candidate in candidates:
        if isinstance(candidate, str) and _SAFE_ERROR_CODE.fullmatch(candidate):
            return candidate
    return None
