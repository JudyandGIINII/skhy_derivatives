"""KRX/KIS/Toss 실 API 어댑터의 네트워크 차단 계약 테스트."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from skhy_research.adapters.providers.kis import KisReadOnlyClient
from skhy_research.adapters.providers.krx import KrxHistoricalDataProvider, KrxReadOnlyClient
from skhy_research.adapters.providers.toss import TossReadOnlyClient
from skhy_research.application.live_capability_probe import run_live_capability_probe
from skhy_research.domain.calendar import local_datetime_to_utc_nanos
from skhy_research.domain.enums import AdjustmentStatus, Venue
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
    ReadOnlyProbeEvidence,
)
from skhy_research.observability.masking import clear_registered_secrets, register_secret
from skhy_research.ports.errors import (
    ProviderAccessDeniedError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderResponseError,
)


class _RecordingSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values
        self.requested_names: list[str] = []

    def get_secret(self, name: str) -> str | None:
        self.requested_names.append(name)
        return self._values.get(name)


def _http_client(handler) -> httpx.Client:  # noqa: ANN001
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_krx_daily_stock_request_uses_auth_header_and_bas_date() -> None:
    secrets = _RecordingSecrets({"KRX_API_KEY": "krx-test-secret"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/svc/apis/sto/stk_bydd_trd"
        assert request.url.params["basDd"] == "20260717"
        assert request.headers["AUTH_KEY"] == "krx-test-secret"
        return httpx.Response(
            200,
            json={
                "OutBlock_1": [
                    {
                        "BAS_DD": "20260717",
                        "ISU_CD": "000660",
                        "TDD_CLSPRC": "500000",
                    }
                ]
            },
        )

    client = KrxReadOnlyClient(secrets, http_client=_http_client(handler), base_url="https://krx.test")
    records = client.fetch_daily_stock_trades(date(2026, 7, 17))

    assert records[0]["ISU_CD"] == "000660"
    assert client.capabilities().supports(ProviderCapability.HISTORICAL_BARS)
    assert secrets.requested_names == ["KRX_API_KEY"]


def test_krx_missing_key_fails_before_network() -> None:
    secrets = _RecordingSecrets({})

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"키 없이 네트워크가 호출됨: {request.url}")

    client = KrxReadOnlyClient(secrets, http_client=_http_client(handler))
    with pytest.raises(ProviderAuthenticationError):
        client.fetch_daily_stock_trades(date(2026, 7, 17))


def test_krx_historical_provider_maps_official_ohlcv_to_bar_contract() -> None:
    secrets = _RecordingSecrets({"KRX_API_KEY": "krx-test-secret"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "OutBlock_1": [
                    {
                        "BAS_DD": "20260717",
                        "ISU_CD": "000660",
                        "TDD_OPNPRC": "495,000",
                        "TDD_HGPRC": "510000",
                        "TDD_LWPRC": "490000",
                        "TDD_CLSPRC": "500000",
                        "ACC_TRDVOL": "1234567",
                        "ACC_TRDVAL": "617283500000",
                    }
                ]
            },
        )

    client = KrxReadOnlyClient(secrets, http_client=_http_client(handler))
    provider = KrxHistoricalDataProvider(client)
    start_utc = local_datetime_to_utc_nanos(date(2026, 7, 17), time(0, 0), Venue.KRX)
    end_utc = local_datetime_to_utc_nanos(date(2026, 7, 17), time(23, 59), Venue.KRX)

    bars = provider.get_bars(
        "SKHY_000660_KRX_COMMON",
        "1d",
        start_utc,
        end_utc,
        AdjustmentStatus.RAW,
    )

    assert len(bars) == 1
    assert bars[0].open == Decimal("495000")
    assert bars[0].close == Decimal("500000")
    assert bars[0].volume == Decimal("1234567")
    assert bars[0].source == "KRX_OPEN_API"
    assert bars[0].construction.method == "VENDOR_PROVIDED"


def test_kis_token_is_cached_and_quote_never_requests_account_secret() -> None:
    secrets = _RecordingSecrets(
        {"KIS_APP_KEY": "kis-app-key", "KIS_APP_SECRET": "kis-app-secret"}
    )
    calls = {"token": 0, "quote": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            calls["token"] += 1
            assert request.method == "POST"
            assert request.url.host == "openapivts.koreainvestment.com"
            assert b"kis-app-secret" in request.content
            return httpx.Response(
                200,
                json={"access_token": "short-lived-test-token", "expires_in": 3600},
            )
        assert request.url.path == "/uapi/domestic-stock/v1/quotations/inquire-price"
        calls["quote"] += 1
        assert request.headers["authorization"] == "Bearer short-lived-test-token"
        assert request.headers["tr_id"] == "FHKST01010100"
        assert request.url.params["FID_INPUT_ISCD"] == "000660"
        return httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "500000"}})

    client = KisReadOnlyClient(secrets, http_client=_http_client(handler))
    assert client.fetch_domestic_quote()["stck_prpr"] == "500000"
    assert client.fetch_domestic_quote()["stck_prpr"] == "500000"

    assert calls == {"token": 1, "quote": 2}
    assert "KIS_ACCOUNT_NO" not in secrets.requested_names
    assert client.capabilities().supports(ProviderCapability.QUOTE_SNAPSHOT)


def test_kis_provider_error_exposes_code_not_response_message() -> None:
    secrets = _RecordingSecrets(
        {"KIS_APP_KEY": "kis-app-key", "KIS_APP_SECRET": "kis-app-secret"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "rt_cd": "1",
                "msg_cd": "EGW00123",
                "msg1": "sensitive upstream detail must not be propagated",
            },
        )

    client = KisReadOnlyClient(secrets, http_client=_http_client(handler))
    with pytest.raises(ProviderResponseError) as exc_info:
        client.fetch_domestic_quote()

    assert "EGW00123" in str(exc_info.value)
    assert "sensitive upstream detail" not in str(exc_info.value)


def test_toss_uses_form_oauth_then_stock_lookup_without_account_header() -> None:
    secrets = _RecordingSecrets(
        {"TOSS_CLIENT_ID": "toss-client", "TOSS_CLIENT_SECRET": "toss-secret"}
    )
    calls = {"token": 0, "stocks": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            form = parse_qs(request.content.decode())
            assert form == {
                "grant_type": ["client_credentials"],
                "client_id": ["toss-client"],
                "client_secret": ["toss-secret"],
            }
            return httpx.Response(200, json={"access_token": "toss-token", "expires_in": 86400})
        calls["stocks"] += 1
        assert request.url.path == "/api/v1/stocks"
        assert request.url.params["symbols"] == "000660"
        assert request.headers["Authorization"] == "Bearer toss-token"
        assert "X-Tossinvest-Account" not in request.headers
        return httpx.Response(
            200,
            json={"result": [{"symbol": "000660", "market": "KOSPI", "currency": "KRW"}]},
        )

    client = TossReadOnlyClient(secrets, http_client=_http_client(handler), base_url="https://toss.test")
    first = client.fetch_stock_info(["000660"])
    second = client.fetch_stock_info(["000660"])

    assert first == second
    assert calls == {"token": 1, "stocks": 2}
    assert client.capabilities().supports(ProviderCapability.INSTRUMENT_LOOKUP)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, ProviderAuthenticationError), (403, ProviderAccessDeniedError)],
)
def test_toss_oauth_maps_auth_and_ip_errors(status: int, error_type: type[Exception]) -> None:
    secrets = _RecordingSecrets(
        {"TOSS_CLIENT_ID": "toss-client", "TOSS_CLIENT_SECRET": "toss-secret"}
    )
    client = TossReadOnlyClient(
        secrets,
        http_client=_http_client(lambda request: httpx.Response(status, json={"secret": "leak"})),
    )

    with pytest.raises(error_type) as exc_info:
        client.fetch_stock_info(["000660"])
    assert "leak" not in str(exc_info.value)


def test_http_429_maps_retry_after_without_body() -> None:
    secrets = _RecordingSecrets({"KRX_API_KEY": "krx-test-secret"})
    client = KrxReadOnlyClient(
        secrets,
        http_client=_http_client(
            lambda request: httpx.Response(
                429,
                headers={"Retry-After": "3.5"},
                json={"api_key": "krx-test-secret"},
            )
        ),
    )

    with pytest.raises(ProviderRateLimitError) as exc_info:
        client.fetch_daily_stock_trades(date(2026, 7, 17))
    assert exc_info.value.retry_after_seconds == 3.5
    assert "krx-test-secret" not in str(exc_info.value)


class _ProbeDouble:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    def capabilities(self) -> ProviderCatalogEntry:
        return ProviderCatalogEntry(
            provider_name="probe-double",
            port_type="reference_data",
            catalog_version="probe-double-reference-data-test-v1",
            capabilities=frozenset({ProviderCapability.INSTRUMENT_LOOKUP}),
            license_terms_url="https://example.test/terms",
            storage_redistribution_allowed=False,
            last_verified_at_utc=0,
        )

    def probe_read_only(self) -> ReadOnlyProbeEvidence:
        if self._error is not None:
            raise self._error
        return ReadOnlyProbeEvidence(
            provider_name="probe-double",
            endpoint="/read-only",
            record_count=1,
            observed_fields=("symbol",),
            measured_latency_ms=12.5,
        )

    def close(self) -> None:
        return None


def test_live_probe_records_health_and_masks_failure() -> None:
    register_secret("probe-secret-canary")
    try:
        success = run_live_capability_probe((_ProbeDouble(),))[0]
        failure = run_live_capability_probe(
            (_ProbeDouble(error=RuntimeError("failed probe-secret-canary")),)
        )[0]
    finally:
        clear_registered_secrets()

    assert success.ok is True
    assert success.catalog_entry.health_status is HealthStatus.HEALTHY
    assert success.catalog_entry.measured_latency_ms == 12.5
    assert failure.ok is False
    assert failure.catalog_entry.health_status is HealthStatus.DOWN
    assert failure.error == "failed ***MASKED***"
