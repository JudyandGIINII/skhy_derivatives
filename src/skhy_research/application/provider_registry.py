"""공급자 등록·조회 레지스트리 (P0-06, FR-02).

`register_broker`는 이름이 정확히 "paper"가 아니면 등록 자체를 거부한다.
이는 `application.config`(broker_mode!=paper 부팅 차단)와 별개의 독립적인
안전장치로, 두 계층 중 하나가 우회되어도 실주문 어댑터가 등록되지 않게 한다
(PRD 7.3/7.4/13.3의 "PaperBrokerProvider만 등록" 요구).
"""

from __future__ import annotations

from collections.abc import Iterator

from skhy_research.domain.provider_capability import ProviderCatalogEntry
from skhy_research.ports.broker import BrokerProvider
from skhy_research.ports.historical_data import HistoricalDataProvider
from skhy_research.ports.market_data import MarketDataProvider, MarketDataSnapshotProvider
from skhy_research.ports.reference_data import ReferenceDataProvider

_ALLOWED_BROKER_NAME = "paper"

AnyProvider = (
    MarketDataProvider
    | MarketDataSnapshotProvider
    | ReferenceDataProvider
    | HistoricalDataProvider
    | BrokerProvider
)


class NonPaperBrokerRegistrationError(RuntimeError):
    pass


class DuplicateProviderRegistrationError(RuntimeError):
    pass


class ProviderNotRegisteredError(RuntimeError):
    pass


class ProviderRegistry:
    def __init__(self) -> None:
        self._market_data: dict[str, MarketDataProvider] = {}
        self._market_data_snapshots: dict[str, MarketDataSnapshotProvider] = {}
        self._reference_data: dict[str, ReferenceDataProvider] = {}
        self._historical_data: dict[str, HistoricalDataProvider] = {}
        self._broker: BrokerProvider | None = None
        self._broker_name: str | None = None

    # --- MarketDataProvider ---
    def register_market_data(self, name: str, provider: MarketDataProvider) -> None:
        if name in self._market_data:
            raise DuplicateProviderRegistrationError(f"market_data '{name}'는 이미 등록됨")
        self._market_data[name] = provider

    def get_market_data(self, name: str) -> MarketDataProvider:
        try:
            return self._market_data[name]
        except KeyError as exc:
            raise ProviderNotRegisteredError(f"market_data '{name}' 미등록") from exc

    # --- MarketDataSnapshotProvider (Phase 1 point-in-time REST) ---
    def register_market_data_snapshot(
        self, name: str, provider: MarketDataSnapshotProvider
    ) -> None:
        if name in self._market_data_snapshots:
            raise DuplicateProviderRegistrationError(
                f"market_data_snapshot '{name}'는 이미 등록됨"
            )
        self._market_data_snapshots[name] = provider

    def get_market_data_snapshot(self, name: str) -> MarketDataSnapshotProvider:
        try:
            return self._market_data_snapshots[name]
        except KeyError as exc:
            raise ProviderNotRegisteredError(f"market_data_snapshot '{name}' 미등록") from exc

    # --- ReferenceDataProvider ---
    def register_reference_data(self, name: str, provider: ReferenceDataProvider) -> None:
        if name in self._reference_data:
            raise DuplicateProviderRegistrationError(f"reference_data '{name}'는 이미 등록됨")
        self._reference_data[name] = provider

    def get_reference_data(self, name: str) -> ReferenceDataProvider:
        try:
            return self._reference_data[name]
        except KeyError as exc:
            raise ProviderNotRegisteredError(f"reference_data '{name}' 미등록") from exc

    # --- HistoricalDataProvider ---
    def register_historical_data(self, name: str, provider: HistoricalDataProvider) -> None:
        if name in self._historical_data:
            raise DuplicateProviderRegistrationError(f"historical_data '{name}'는 이미 등록됨")
        self._historical_data[name] = provider

    def get_historical_data(self, name: str) -> HistoricalDataProvider:
        try:
            return self._historical_data[name]
        except KeyError as exc:
            raise ProviderNotRegisteredError(f"historical_data '{name}' 미등록") from exc

    # --- BrokerProvider: paper 전용 ---
    def register_broker(self, name: str, provider: BrokerProvider) -> None:
        if name != _ALLOWED_BROKER_NAME:
            raise NonPaperBrokerRegistrationError(
                f"broker '{name}' 등록 거부됨. v1은 '{_ALLOWED_BROKER_NAME}'만 등록 가능하다"
            )
        if self._broker is not None:
            raise DuplicateProviderRegistrationError("broker는 한 번만 등록할 수 있다")
        self._broker = provider
        self._broker_name = name

    def get_broker(self) -> BrokerProvider:
        if self._broker is None:
            raise ProviderNotRegisteredError("broker가 등록되지 않았다")
        return self._broker

    # --- catalog 조회 (FR-02) ---
    def full_catalog(self) -> list[ProviderCatalogEntry]:
        return [provider.capabilities() for _, _, provider in self.iter_providers()]

    def iter_providers(self) -> Iterator[tuple[str, str, AnyProvider]]:
        """(port_type, provider_name, provider) 삼중항을 순회한다. capability probe·건강상태 점검용."""
        for name, provider in self._market_data.items():
            yield ("market_data", name, provider)
        for name, provider in self._market_data_snapshots.items():
            yield ("market_data_snapshot", name, provider)
        for name, provider in self._reference_data.items():
            yield ("reference_data", name, provider)
        for name, provider in self._historical_data.items():
            yield ("historical_data", name, provider)
        if self._broker is not None and self._broker_name is not None:
            yield ("broker", self._broker_name, self._broker)
