"""6개 공급자(KRX/KIS/Toss/공식공시/Naver/Yahoo)의 sanitized fixture 등록 (P0-07).

여기 payload는 실제 API 응답을 그대로 기록한 것이 아니라 스키마 검증을 위해
구조적으로 유사하게 구성한 합성(synthetic) 예시다. 실제 응답 기록은 사용자가
조회 전용 키를 주입한 환경에서 capability probe를 실행한 뒤
`tests/fixtures/sanitized/`에 정제 저장한다 (G-02).

역할 배분은 PRD 7.1 우선순위표를 따른다: KRX는 기준정보·과거데이터(실시간
주문판단 미사용), KIS는 실시간 기본, Toss는 교차검증 보조, 공식공시는
기준정보, Naver/Yahoo는 장기 백필·대조 전용(실시간 주문판단 미사용).
"""

from __future__ import annotations

import time
from decimal import Decimal

from skhy_research.adapters.providers.fixture_historical_data import FixtureHistoricalDataProvider
from skhy_research.adapters.providers.fixture_market_data import FixtureMarketDataProvider
from skhy_research.adapters.providers.fixture_reference_data import FixtureReferenceDataProvider
from skhy_research.adapters.providers.fixture_support import FixtureCallGateway, FixtureScenario
from skhy_research.application.provider_registry import ProviderRegistry
from skhy_research.domain.provider_capability import (
    ConnectionHealth,
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)

_NOW = time.time_ns()


def _catalog(
    name: str, port_type: str, capabilities: frozenset[ProviderCapability], license_url: str
) -> ProviderCatalogEntry:
    return ProviderCatalogEntry(
        provider_name=name,
        port_type=port_type,
        capabilities=capabilities,
        license_terms_url=license_url,
        storage_redistribution_allowed=False,
        last_verified_at_utc=_NOW,
        health_status=HealthStatus.HEALTHY,
    )


def build_fixture_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()

    # --- KRX: 공식 일별·기준정보. require_auth=False (replay 전용, 실제 인증은 P0-07 계약테스트에서 별도 검증) ---
    krx_gateway = FixtureCallGateway("krx", require_auth=False)
    krx_reference = FixtureReferenceDataProvider(
        catalog_entry=_catalog(
            "krx",
            "reference_data",
            frozenset({ProviderCapability.INSTRUMENT_MASTER}),
            "https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd",
        ),
        gateway=krx_gateway,
        instrument_master_scenario=FixtureScenario(
            payload=[
                {
                    "instrument_id": "SKHY_000660_KRX_COMMON",
                    "asset_class": "COMMON_STOCK",
                    "primary_venue": "KRX",
                    "display_name": "SK hynix",
                    "is_active": True,
                    "listed_at_utc": 1_000_000_000_000_000_000,
                }
            ]
        ),
    )
    registry.register_reference_data("krx", krx_reference)

    krx_historical = FixtureHistoricalDataProvider(
        catalog_entry=_catalog(
            "krx",
            "historical_data",
            frozenset({ProviderCapability.HISTORICAL_BARS}),
            "https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd",
        ),
        gateway=krx_gateway,
        bars_scenario=FixtureScenario(payload=[_sample_bar_row("KRX", "krx")]),
    )
    registry.register_historical_data("krx", krx_historical)

    # --- KIS: 실시간 시세 기본 ---
    kis_gateway = FixtureCallGateway("kis", require_auth=False)
    kis_market = FixtureMarketDataProvider(
        catalog_entry=_catalog(
            "kis",
            "market_data",
            frozenset({ProviderCapability.QUOTE_STREAM, ProviderCapability.TRADE_STREAM}),
            "https://apiportal.koreainvestment.com/apiservice-category",
        ),
        gateway=kis_gateway,
        connection_health=ConnectionHealth(is_connected=True, measured_latency_ms=180.0),
        quotes_scenario=FixtureScenario(payload=[_sample_quote_row("KRX", "kis")]),
    )
    registry.register_market_data("kis", kis_market)

    # --- Toss: 교차검증 보조 ---
    toss_gateway = FixtureCallGateway("toss", require_auth=False)
    toss_market = FixtureMarketDataProvider(
        catalog_entry=_catalog(
            "toss",
            "market_data",
            frozenset({ProviderCapability.QUOTE_STREAM}),
            "https://developers.tossinvest.com/llms.txt",
        ),
        gateway=toss_gateway,
        connection_health=ConnectionHealth(is_connected=True, measured_latency_ms=210.0),
        quotes_scenario=FixtureScenario(payload=[_sample_quote_row("KRX", "toss")]),
    )
    registry.register_market_data("toss", toss_market)

    # --- 공식공시(SEC/KIND/HKEX/Citi/KSD): 기준정보(ADR 비율·전환 상태) ---
    official_gateway = FixtureCallGateway("official_filings", require_auth=False)
    official_reference = FixtureReferenceDataProvider(
        catalog_entry=_catalog(
            "official_filings",
            "reference_data",
            frozenset({ProviderCapability.ADR_RATIO_CONVERSION_STATUS}),
            "https://www.sec.gov/Archives/edgar/data/2120882/000119312526299963/d32785d424b4.htm",
        ),
        gateway=official_gateway,
        conversion_status_scenarios={
            "SKHY_CONVERSION": FixtureScenario(
                payload={
                    "source": "KIND",
                    "venue": "REFERENCE",
                    "symbol": "SKHY_CONVERSION",
                    "event_time_utc": _NOW,
                    "received_time_utc": _NOW,
                    "currency": None,
                    "currency_na_reason": "REFERENCE_ONLY_NO_PRICE",
                    "session": "REFERENCE",
                    "is_delayed": False,
                    "adjustment_status": "NOT_APPLICABLE",
                    "status": "UNKNOWN",
                    "adr_ratio_common_to_adr": "10",
                    "evidence_url": "https://kind.krx.co.kr/external/2026/07/13/000494/20260713001138/11315.htm",
                    "confirmed_at_utc": _NOW,
                }
            )
        },
    )
    registry.register_reference_data("official_filings", official_reference)

    # --- Naver/Yahoo: 장기 일봉 백필·대조 전용 (실시간 주문판단 미사용) ---
    naver_gateway = FixtureCallGateway("naver", require_auth=False)
    naver_historical = FixtureHistoricalDataProvider(
        catalog_entry=_catalog(
            "naver",
            "historical_data",
            frozenset({ProviderCapability.HISTORICAL_BARS}),
            "https://finance.naver.com",
        ),
        gateway=naver_gateway,
        bars_scenario=FixtureScenario(payload=[_sample_bar_row("KRX", "naver")]),
    )
    registry.register_historical_data("naver", naver_historical)

    yahoo_gateway = FixtureCallGateway("yahoo", require_auth=False)
    yahoo_historical = FixtureHistoricalDataProvider(
        catalog_entry=_catalog(
            "yahoo",
            "historical_data",
            frozenset({ProviderCapability.HISTORICAL_BARS}),
            "https://finance.yahoo.com",
        ),
        gateway=yahoo_gateway,
        bars_scenario=FixtureScenario(payload=[_sample_bar_row("NASDAQ", "yahoo")]),
    )
    registry.register_historical_data("yahoo", yahoo_historical)

    return registry


def _sample_bar_row(venue: str, source: str) -> dict:
    return {
        "source": source,
        "venue": venue,
        "symbol": "000660" if venue == "KRX" else "SKHY",
        "event_time_utc": _NOW,
        "received_time_utc": _NOW,
        "currency": "KRW" if venue == "KRX" else "USD",
        "session": "REGULAR",
        "is_delayed": True,
        "adjustment_status": "RAW",
        "instrument_id": "SKHY_000660_KRX_COMMON" if venue == "KRX" else "SKHY_NASDAQ_ADR",
        "period": "1d",
        "open": Decimal("200000"),
        "high": Decimal("205000"),
        "low": Decimal("198000"),
        "close": Decimal("203000"),
        "volume": Decimal("1500000"),
        "is_adjusted": False,
        "construction": {"method": "VENDOR_PROVIDED", "source_segment": f"{source}:sanitized_fixture"},
        "bar_close_time_utc": _NOW,
    }


def _sample_quote_row(venue: str, source: str) -> dict:
    return {
        "source": source,
        "venue": venue,
        "symbol": "000660",
        "event_time_utc": _NOW,
        "received_time_utc": _NOW + 1_000_000,
        "currency": "KRW",
        "session": "REGULAR",
        "is_delayed": True,
        "adjustment_status": "RAW",
        "instrument_id": "SKHY_000660_KRX_COMMON",
        "bid_price": Decimal("202900"),
        "ask_price": Decimal("203000"),
        "bid_size": Decimal("120"),
        "ask_size": Decimal("95"),
    }
