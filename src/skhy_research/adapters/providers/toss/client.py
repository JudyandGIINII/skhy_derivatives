"""토스증권 OAuth·종목 기본정보 조회 전용 어댑터."""

from __future__ import annotations

import re
import time
from typing import Any

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

_PROVIDER_NAME = "toss"
_DEFAULT_BASE_URL = "https://openapi.tossinvest.com"
_TOKEN_PATH = "/oauth2/token"
_STOCKS_PATH = "/api/v1/stocks"
_PRICES_PATH = "/api/v1/prices"
_ORDERBOOK_PATH = "/api/v1/orderbook"
_SYMBOL = re.compile(r"^[A-Za-z0-9.\-]+$")


class TossReadOnlyClient:
    """계좌 header가 필요 없는 종목 기준정보 API만 노출한다."""

    def __init__(
        self,
        secret_provider: SecretProvider,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._secret_provider = secret_provider
        self._base_url = base_url.rstrip("/")
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
            catalog_version="toss-market-data-v2",
            capabilities=frozenset(
                {ProviderCapability.INSTRUMENT_LOOKUP, ProviderCapability.QUOTE_SNAPSHOT}
            ),
            license_terms_url="https://openapi.tossinvest.com/openapi-docs/overview.md",
            storage_redistribution_allowed=False,
            last_verified_at_utc=time.time_ns(),
            health_status=HealthStatus.UNKNOWN,
        )

    def fetch_stock_info(self, symbols: list[str]) -> list[dict[str, Any]]:
        _validate_symbols(symbols)
        token = self._get_access_token()
        payload = request_json(
            _PROVIDER_NAME,
            lambda: self._client.get(
                f"{self._base_url}{_STOCKS_PATH}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                params={"symbols": ",".join(symbols)},
            ),
        )
        result = payload.get("result")
        if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
            raise ProviderResponseError(_PROVIDER_NAME, error_code="missing-result")
        return result

    def fetch_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        """timestamp가 포함된 현재가를 최대 200종목까지 읽는다."""

        _validate_symbols(symbols)
        payload = self._authenticated_get(_PRICES_PATH, {"symbols": ",".join(symbols)})
        result = payload.get("result")
        if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
            raise ProviderResponseError(_PROVIDER_NAME, error_code="missing-result")
        return result

    def fetch_orderbook(self, symbol: str) -> dict[str, Any]:
        """MarketQuote 매핑용 최우선 매수·매도호가를 읽는다."""

        _validate_symbols([symbol])
        payload = self._authenticated_get(_ORDERBOOK_PATH, {"symbol": symbol})
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ProviderResponseError(_PROVIDER_NAME, error_code="missing-result")
        return result

    def probe_read_only(self, symbol: str = "000660") -> ReadOnlyProbeEvidence:
        started = time.perf_counter()
        records = self.fetch_stock_info([symbol])
        if not records or records[0].get("symbol") != symbol:
            raise ProviderResponseError(_PROVIDER_NAME, error_code="instrument-not-found")
        return ReadOnlyProbeEvidence(
            provider_name=_PROVIDER_NAME,
            endpoint=_STOCKS_PATH,
            record_count=len(records),
            observed_fields=tuple(sorted(records[0].keys())),
            measured_latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _get_access_token(self) -> str:
        now = time.monotonic()
        if self._access_token is not None and now < self._token_valid_until:
            return self._access_token
        client_id = require_secret(self._secret_provider, _PROVIDER_NAME, "TOSS_CLIENT_ID")
        client_secret = require_secret(self._secret_provider, _PROVIDER_NAME, "TOSS_CLIENT_SECRET")
        payload = request_json(
            _PROVIDER_NAME,
            lambda: self._client.post(
                f"{self._base_url}{_TOKEN_PATH}",
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            ),
        )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderAuthenticationError(_PROVIDER_NAME)
        register_secret(token)
        expires_in = _positive_float(payload.get("expires_in"), default=3600.0)
        self._access_token = token
        self._token_valid_until = now + max(1.0, expires_in - 60.0)
        return token

    def _authenticated_get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        token = self._get_access_token()
        return request_json(
            _PROVIDER_NAME,
            lambda: self._client.get(
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                params=params,
            ),
        )


def _positive_float(value: object, *, default: float) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _validate_symbols(symbols: list[str]) -> None:
    if not symbols or len(symbols) > 200 or any(not _SYMBOL.fullmatch(item) for item in symbols):
        raise ValueError("symbols는 1~200개의 영문·숫자·점·하이픈만 허용한다")
