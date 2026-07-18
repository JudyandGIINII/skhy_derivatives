"""P0-06 검증: 4개 포트 레지스트리, paper 전용 broker 게이트, UNSUPPORTED_CAPABILITY."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.application.provider_registry import (
    DuplicateProviderRegistrationError,
    NonPaperBrokerRegistrationError,
    ProviderNotRegisteredError,
    ProviderRegistry,
)
from skhy_research.domain.execution import AccountSnapshot, OrderIntent, PaperFill
from skhy_research.domain.provider_capability import (
    ConnectionHealth,
    HealthStatus,
    ProviderCapability,
    ProviderCatalogEntry,
)
from skhy_research.ports.errors import UnsupportedCapabilityError

_NOW = 1_800_000_000_000_000_000


def _catalog_entry(name: str, port_type: str, capabilities: frozenset[ProviderCapability]) -> ProviderCatalogEntry:
    return ProviderCatalogEntry(
        provider_name=name,
        port_type=port_type,
        catalog_version=f"{name}-{port_type}-test-v1",
        capabilities=capabilities,
        license_terms_url="https://example.com/tos",
        storage_redistribution_allowed=False,
        last_verified_at_utc=_NOW,
        health_status=HealthStatus.HEALTHY,
    )


class _FakeMarketData:
    def __init__(self, name: str, capabilities: frozenset[ProviderCapability]) -> None:
        self._entry = _catalog_entry(name, "market_data", capabilities)

    def capabilities(self) -> ProviderCatalogEntry:
        return self._entry

    def connection_health(self) -> ConnectionHealth:
        return ConnectionHealth(is_connected=True)

    def subscribe_quotes(self, instrument_ids):  # noqa: ANN001
        if not self._entry.supports(ProviderCapability.QUOTE_STREAM):
            raise UnsupportedCapabilityError(self._entry.provider_name, ProviderCapability.QUOTE_STREAM)

        async def _empty():
            return
            yield  # pragma: no cover

        return _empty()

    def subscribe_trades(self, instrument_ids):  # noqa: ANN001
        raise UnsupportedCapabilityError(self._entry.provider_name, ProviderCapability.TRADE_STREAM)

    async def unsubscribe(self, instrument_ids) -> None:  # noqa: ANN001
        return None


class _FakeReferenceData:
    def __init__(self, name: str) -> None:
        self._entry = _catalog_entry(name, "reference_data", frozenset({ProviderCapability.INSTRUMENT_MASTER}))

    def capabilities(self) -> ProviderCatalogEntry:
        return self._entry

    def get_instrument_master(self, as_of_utc: int) -> list:
        return []

    def get_corporate_actions(self, instrument_id: str, as_of_utc: int) -> list:
        return []

    def get_conversion_status(self, instrument_id: str):
        raise UnsupportedCapabilityError(
            self._entry.provider_name, ProviderCapability.ADR_RATIO_CONVERSION_STATUS
        )

    def get_fund_snapshot(self, fund_id: str):
        raise UnsupportedCapabilityError(self._entry.provider_name, ProviderCapability.FUND_SNAPSHOT)


class _FakeHistoricalData:
    def __init__(self, name: str) -> None:
        self._entry = _catalog_entry(name, "historical_data", frozenset({ProviderCapability.HISTORICAL_BARS}))

    def capabilities(self) -> ProviderCatalogEntry:
        return self._entry

    def get_bars(self, instrument_id, period, start_utc, end_utc, adjustment) -> list:  # noqa: ANN001
        return []


class _FakePaperBroker:
    def __init__(self) -> None:
        self._entry = _catalog_entry(
            "paper",
            "broker",
            frozenset(
                {
                    ProviderCapability.ACCOUNT_SNAPSHOT,
                    ProviderCapability.ORDER_SUBMIT,
                    ProviderCapability.ORDER_CANCEL,
                    ProviderCapability.FILL_EVENTS,
                }
            ),
        )

    def capabilities(self) -> ProviderCatalogEntry:
        return self._entry

    def account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account_id="paper-1",
            as_of_utc=_NOW,
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        )

    def submit_order(self, order: OrderIntent) -> str:
        return f"paper-ref-{order.order_id}"

    def cancel_order(self, order_id: str) -> None:
        return None

    def poll_fills(self, order_id: str) -> list[PaperFill]:
        return []


def test_market_data_register_and_get_round_trip() -> None:
    registry = ProviderRegistry()
    provider = _FakeMarketData("kis", frozenset({ProviderCapability.QUOTE_STREAM}))
    registry.register_market_data("kis", provider)

    assert registry.get_market_data("kis") is provider


def test_get_unregistered_market_data_raises() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderNotRegisteredError):
        registry.get_market_data("kis")


def test_duplicate_market_data_registration_raises() -> None:
    registry = ProviderRegistry()
    registry.register_market_data("kis", _FakeMarketData("kis", frozenset()))
    with pytest.raises(DuplicateProviderRegistrationError):
        registry.register_market_data("kis", _FakeMarketData("kis", frozenset()))


def test_register_broker_accepts_only_paper() -> None:
    registry = ProviderRegistry()
    registry.register_broker("paper", _FakePaperBroker())
    assert registry.get_broker().capabilities().provider_name == "paper"


def test_register_broker_rejects_non_paper_name() -> None:
    registry = ProviderRegistry()
    with pytest.raises(NonPaperBrokerRegistrationError):
        registry.register_broker("kis", _FakePaperBroker())


def test_register_broker_twice_raises_even_if_both_paper() -> None:
    registry = ProviderRegistry()
    registry.register_broker("paper", _FakePaperBroker())
    with pytest.raises(DuplicateProviderRegistrationError):
        registry.register_broker("paper", _FakePaperBroker())


def test_get_broker_before_registration_raises() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderNotRegisteredError):
        registry.get_broker()


def test_full_catalog_aggregates_all_registered_providers() -> None:
    registry = ProviderRegistry()
    registry.register_market_data("kis", _FakeMarketData("kis", frozenset({ProviderCapability.QUOTE_STREAM})))
    registry.register_reference_data("krx", _FakeReferenceData("krx"))
    registry.register_historical_data("krx_hist", _FakeHistoricalData("krx_hist"))
    registry.register_broker("paper", _FakePaperBroker())

    names = {entry.provider_name for entry in registry.full_catalog()}
    assert names == {"kis", "krx", "krx_hist", "paper"}


def test_unsupported_capability_raised_explicitly_not_empty_data() -> None:
    provider = _FakeMarketData("toss", frozenset())  # QUOTE_STREAM 미지원
    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        provider.subscribe_quotes(["000660"])

    assert exc_info.value.provider_name == "toss"
    assert exc_info.value.capability == ProviderCapability.QUOTE_STREAM


def test_provider_catalog_entry_supports_reflects_capability_set() -> None:
    entry = _catalog_entry("krx", "reference_data", frozenset({ProviderCapability.INSTRUMENT_MASTER}))
    assert entry.supports(ProviderCapability.INSTRUMENT_MASTER) is True
    assert entry.supports(ProviderCapability.ADR_RATIO_CONVERSION_STATUS) is False
