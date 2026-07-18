"""한국투자증권 OAuth·국내주식 현재가 조회 전용 어댑터."""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx

from skhy_research.adapters.providers.http_support import request_json, require_secret
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
    ReadOnlyProbeEvidence,
)
from skhy_research.observability.masking import register_secret
from skhy_research.ports.errors import ProviderAuthenticationError, ProviderResponseError
from skhy_research.ports.secrets import SecretProvider

_PROVIDER_NAME = "kis"
_BASE_URLS = {
    "vps": "https://openapivts.koreainvestment.com:29443",
    "prod": "https://openapi.koreainvestment.com:9443",
}
_TOKEN_PATH = "/oauth2/tokenP"
_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"

KisEnvironment = Literal["vps", "prod"]


class KisReadOnlyClient:
    """OAuth token을 메모리에만 cache하고 공개 시세만 조회한다."""

    def __init__(
        self,
        secret_provider: SecretProvider,
        *,
        environment: KisEnvironment = "vps",
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if environment not in _BASE_URLS:
            raise ValueError("KIS environment는 'vps' 또는 'prod'여야 한다")
        self._secret_provider = secret_provider
        self._base_url = (base_url or _BASE_URLS[environment]).rstrip("/")
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None
        self._access_token: str | None = None
        self._token_valid_until = 0.0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def capabilities(self) -> ProviderCatalogEntry:
        return ProviderCatalogEntry(
            provider_name=_PROVIDER_NAME,
            port_type="market_data",
            catalog_version="kis-market-data-v1",
            capabilities=frozenset({ProviderCapability.QUOTE_SNAPSHOT}),
            license_terms_url="https://apiportal.koreainvestment.com/intro",
            storage_redistribution_allowed=False,
            last_verified_at_utc=time.time_ns(),
            health_status=HealthStatus.UNKNOWN,
        )

    def fetch_domestic_quote(self, symbol: str = "000660", market: str = "J") -> dict[str, Any]:
        app_key = require_secret(self._secret_provider, _PROVIDER_NAME, "KIS_APP_KEY")
        app_secret = require_secret(self._secret_provider, _PROVIDER_NAME, "KIS_APP_SECRET")
        token = self._get_access_token(app_key, app_secret)
        payload = request_json(
            _PROVIDER_NAME,
            lambda: self._client.get(
                f"{self._base_url}{_QUOTE_PATH}",
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey": app_key,
                    "appsecret": app_secret,
                    "tr_id": "FHKST01010100",
                    "custtype": "P",
                    "tr_cont": "",
                    "Content-Type": "application/json",
                    "Accept": "text/plain",
                    "charset": "UTF-8",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
                    ),
                },
                params={"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": symbol},
            ),
        )
        if str(payload.get("rt_cd")) != "0":
            error_code = payload.get("msg_cd")
            raise ProviderResponseError(
                _PROVIDER_NAME,
                error_code=str(error_code) if error_code is not None else None,
            )
        output = payload.get("output")
        if not isinstance(output, dict):
            raise ProviderResponseError(_PROVIDER_NAME, error_code="missing-output")
        return output

    def probe_read_only(self, symbol: str = "000660") -> ReadOnlyProbeEvidence:
        started = time.perf_counter()
        quote = self.fetch_domestic_quote(symbol)
        if "stck_prpr" not in quote:
            raise ProviderResponseError(_PROVIDER_NAME, error_code="missing-stck-prpr")
        return ReadOnlyProbeEvidence(
            provider_name=_PROVIDER_NAME,
            endpoint=_QUOTE_PATH,
            record_count=1,
            observed_fields=tuple(sorted(quote.keys())),
            measured_latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _get_access_token(self, app_key: str, app_secret: str) -> str:
        now = time.monotonic()
        if self._access_token is not None and now < self._token_valid_until:
            return self._access_token
        payload = request_json(
            _PROVIDER_NAME,
            lambda: self._client.post(
                f"{self._base_url}{_TOKEN_PATH}",
                headers={"Content-Type": "application/json", "Accept": "text/plain"},
                json={
                    "grant_type": "client_credentials",
                    "appkey": app_key,
                    "appsecret": app_secret,
                },
            ),
        )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderAuthenticationError(_PROVIDER_NAME)
        register_secret(token)
        expires_in = _positive_float(payload.get("expires_in"), default=86400.0)
        self._access_token = token
        buffer = 21600.0 if expires_in > 21600.0 else 60.0
        self._token_valid_until = now + max(1.0, expires_in - buffer)
        return token


def _positive_float(value: object, *, default: float) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default
