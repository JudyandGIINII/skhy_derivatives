"""KRX Data Marketplace 일별매매정보 조회 전용 어댑터."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from skhy_research.adapters.providers.http_support import request_json, require_secret
from skhy_research.adapters.providers.krx.research_data import (
    KrxResearchDataset,
    KrxResearchDatasetAvailability,
    reject_unlisted_open_api_dataset,
    research_dataset_availability,
)
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
_DAILY_ETF_PATH = "/svc/apis/etp/etf_bydd_trd"
_DAILY_ETN_PATH = "/svc/apis/etp/etn_bydd_trd"
_DAILY_KRX_INDEX_PATH = "/svc/apis/idx/krx_dd_trd"
_DAILY_KOSPI_INDEX_PATH = "/svc/apis/idx/kospi_dd_trd"
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
            catalog_version="krx-historical-data-v2",
            capabilities=frozenset(
                {ProviderCapability.HISTORICAL_BARS, ProviderCapability.INSTRUMENT_MASTER}
            ),
            license_terms_url="https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd",
            storage_redistribution_allowed=False,
            last_verified_at_utc=time.time_ns(),
            health_status=HealthStatus.UNKNOWN,
        )

    def fetch_daily_stock_trades(self, trading_date: date) -> list[dict[str, Any]]:
        return self._fetch_daily_records(_DAILY_STOCK_PATH, trading_date)

    def fetch_daily_etf_trades(self, trading_date: date) -> list[dict[str, Any]]:
        """ETF 일별매매정보 원문을 조회한다 (`/svc/apis/etp/etf_bydd_trd`)."""
        return self._fetch_daily_records(_DAILY_ETF_PATH, trading_date)

    def fetch_daily_etn_trades(self, trading_date: date) -> list[dict[str, Any]]:
        """ETN 일별매매정보 원문을 조회한다 (`/svc/apis/etp/etn_bydd_trd`)."""
        return self._fetch_daily_records(_DAILY_ETN_PATH, trading_date)

    def fetch_daily_krx_index_trades(self, trading_date: date) -> list[dict[str, Any]]:
        """KRX 시리즈 일별시세정보를 조회한다."""
        return self._fetch_daily_records(_DAILY_KRX_INDEX_PATH, trading_date)

    def fetch_daily_kospi_index_trades(self, trading_date: date) -> list[dict[str, Any]]:
        """KOSPI 시리즈 일별시세정보를 조회한다."""
        return self._fetch_daily_records(_DAILY_KOSPI_INDEX_PATH, trading_date)

    def research_dataset_availability(
        self,
    ) -> tuple[KrxResearchDatasetAvailability, ...]:
        """일반수급·공매도의 공식 Open API 제공 범위를 반환한다."""

        return research_dataset_availability()

    def fetch_investor_net_buy_notional(self, trading_date: date) -> list[dict[str, Any]]:
        """미제공 항목의 추측 엔드포인트 호출을 fail-closed로 차단한다."""

        del trading_date
        reject_unlisted_open_api_dataset(KrxResearchDataset.INVESTOR_NET_BUY)
        return []

    def fetch_short_selling_comprehensive(
        self, trading_date: date, *, symbol: str
    ) -> list[dict[str, Any]]:
        """[MDCSTAT300]은 Open API가 아니므로 수동 CSV 경로만 허용한다."""

        del trading_date, symbol
        reject_unlisted_open_api_dataset(
            KrxResearchDataset.SHORT_SELLING_COMPREHENSIVE
        )
        return []

    def _fetch_daily_records(
        self, endpoint_path: str, trading_date: date
    ) -> list[dict[str, Any]]:
        api_key = require_secret(self._secret_provider, _PROVIDER_NAME, "KRX_API_KEY")
        payload = request_json(
            _PROVIDER_NAME,
            lambda: self._client.get(
                f"{self._base_url}{endpoint_path}",
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
