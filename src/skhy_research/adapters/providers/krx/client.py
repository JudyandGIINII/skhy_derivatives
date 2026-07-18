"""KRX Data Marketplace 일별매매정보 조회 전용 어댑터."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from skhy_research.adapters.providers.http_support import request_json, require_secret
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
    ReadOnlyProbeEvidence,
)
from skhy_research.ports.errors import ProviderResponseError
from skhy_research.ports.secrets import SecretProvider

_PROVIDER_NAME = "krx"
_DEFAULT_BASE_URL = "https://data-dbg.krx.co.kr"
_DAILY_STOCK_PATH = "/svc/apis/sto/stk_bydd_trd"
_SEOUL = ZoneInfo("Asia/Seoul")


class KrxReadOnlyClient:
    """KRX 유가증권 일별 OHLCV 원문을 조회한다.

    API key는 호출 시점에 ``SecretProvider``에서만 읽는다. 이 클래스에는
    쓰기·주문·계좌 경로가 없다.
    """

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

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def capabilities(self) -> ProviderCatalogEntry:
        return ProviderCatalogEntry(
            provider_name=_PROVIDER_NAME,
            port_type="historical_data",
            catalog_version="krx-historical-data-v1",
            capabilities=frozenset({ProviderCapability.HISTORICAL_BARS}),
            license_terms_url="https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd",
            storage_redistribution_allowed=False,
            last_verified_at_utc=time.time_ns(),
            health_status=HealthStatus.UNKNOWN,
        )

    def fetch_daily_stock_trades(self, trading_date: date) -> list[dict[str, Any]]:
        api_key = require_secret(self._secret_provider, _PROVIDER_NAME, "KRX_API_KEY")
        payload = request_json(
            _PROVIDER_NAME,
            lambda: self._client.get(
                f"{self._base_url}{_DAILY_STOCK_PATH}",
                headers={"AUTH_KEY": api_key, "Accept": "application/json"},
                params={"basDd": trading_date.strftime("%Y%m%d")},
            ),
        )
        records = payload.get("OutBlock_1")
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            error_code = payload.get("resultCode") or payload.get("code")
            raise ProviderResponseError(
                _PROVIDER_NAME,
                error_code=str(error_code) if error_code is not None else None,
            )
        return records

    def probe_read_only(self, *, max_lookback_days: int = 7) -> ReadOnlyProbeEvidence:
        started = time.perf_counter()
        candidate = datetime.now(_SEOUL).date() - timedelta(days=1)
        records: list[dict[str, Any]] = []
        for _ in range(max_lookback_days):
            if candidate.weekday() < 5:
                records = self.fetch_daily_stock_trades(candidate)
                if records:
                    break
            candidate -= timedelta(days=1)
        if not records:
            raise ProviderResponseError(_PROVIDER_NAME, error_code="no-recent-trading-records")
        return ReadOnlyProbeEvidence(
            provider_name=_PROVIDER_NAME,
            endpoint=_DAILY_STOCK_PATH,
            record_count=len(records),
            observed_fields=tuple(sorted(records[0].keys())),
            measured_latency_ms=(time.perf_counter() - started) * 1000,
        )
