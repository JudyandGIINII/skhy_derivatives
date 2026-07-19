"""KRX 일반수급·공매도 미제공/CSV/append-only 계약."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from skhy_research.adapters.providers.krx import (
    KrxOpenApiDatasetUnavailableError,
    KrxOpenApiProvisionStatus,
    KrxReadOnlyClient,
    KrxResearchDataset,
)
from skhy_research.application.krx_general_flow_backfill import (
    load_krx_investor_net_buy_csv,
    load_krx_mdcstat300_short_sale_csv,
    persist_investor_flow_append_only,
    persist_short_sale_append_only,
)
from skhy_research.features.g9_idiosyncratic_flow import InvestorFlowScope


class _SecretProvider:
    def get_secret(self, name: str) -> str | None:
        return "secret" if name == "KRX_API_KEY" else None


def test_unlisted_research_datasets_are_never_sent_to_guessed_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = KrxReadOnlyClient(
        _SecretProvider(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    availability = {item.dataset: item for item in client.research_dataset_availability()}

    assert (
        availability[KrxResearchDataset.INVESTOR_NET_BUY].status
        is KrxOpenApiProvisionStatus.NOT_IN_OFFICIAL_CATALOG
    )
    assert (
        availability[KrxResearchDataset.SHORT_SELLING_COMPREHENSIVE].status
        is KrxOpenApiProvisionStatus.NOT_IN_OFFICIAL_CATALOG
    )
    with pytest.raises(KrxOpenApiDatasetUnavailableError):
        client.fetch_investor_net_buy_notional(date(2026, 7, 16))
    with pytest.raises(KrxOpenApiDatasetUnavailableError):
        client.fetch_short_selling_comprehensive(date(2026, 7, 16), symbol="000660")
    assert requests == []


def test_manual_investor_csv_preserves_sign_scope_and_lineage(tmp_path) -> None:
    source = tmp_path / "investor.csv"
    source.write_text(
        "일자,기관 합계,외국인 합계,전체\n"
        '"2026/07/15","-1,200","900","0"\n'
        '"2026/07/16","300","-250","0"\n',
        encoding="utf-8",
    )

    loaded = load_krx_investor_net_buy_csv(
        source, scope=InvestorFlowScope.MARKET
    )
    foreign = [item for item in loaded.observations if item.investor == "외국인 합계"]

    assert loaded.trading_day_count == 2
    assert [item.net_buy_notional for item in foreign] == [900, -250]
    assert all(item.scope is InvestorFlowScope.MARKET for item in foreign)
    assert all(loaded.file_sha256[:16] in item.input_record_id for item in foreign)

    first = persist_investor_flow_append_only(loaded, tmp_path / "data")
    second = persist_investor_flow_append_only(loaded, tmp_path / "data")
    assert first.duplicate is False
    assert second.duplicate is True
    assert (tmp_path / "data" / "raw" / first.dataset / f"{loaded.file_sha256}.csv").exists()
    assert (tmp_path / "data" / "lineage" / first.dataset / f"{loaded.file_sha256}.json").exists()


def test_mdcstat300_loader_keeps_volume_and_balance_without_synthesis(tmp_path) -> None:
    source = tmp_path / "short.csv"
    source.write_text(
        "일자,종목코드,공매도 거래량,공매도 잔고수량\n"
        "2026-07-15,000660,1200,5000\n"
        "2026-07-16,000660,1300,4800\n",
        encoding="utf-8",
    )

    loaded = load_krx_mdcstat300_short_sale_csv(source)

    assert loaded.trading_day_count == 2
    assert loaded.observations[0].short_volume == 1200
    assert loaded.observations[0].short_balance == 5000
    artifact = persist_short_sale_append_only(loaded, tmp_path / "data")
    assert artifact.record_count == 2
    assert artifact.dataset.endswith("/000660")


def test_mdcstat300_missing_both_required_columns_fails_closed(tmp_path) -> None:
    source = tmp_path / "bad.csv"
    source.write_text("일자,종가\n2026-07-16,100000\n", encoding="utf-8")

    with pytest.raises(ValueError, match="거래량 또는 잔고"):
        load_krx_mdcstat300_short_sale_csv(source)
