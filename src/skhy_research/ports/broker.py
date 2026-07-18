"""계좌·주문·취소·체결 이벤트 추상 계약 (PRD 7.4 BrokerProvider).

v1 의존성 주입 컨테이너에는 `PaperBrokerProvider`만 등록한다. 이 Protocol 자체는
포트 계약일 뿐이며, 실제 등록 제한은 `application.provider_registry`가 강제한다.
"""

from __future__ import annotations

from typing import Protocol

from skhy_research.domain.execution import AccountSnapshot, OrderIntent, PaperFill
from skhy_research.domain.provider_capability import ProviderCatalogEntry


class BrokerProvider(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def account_snapshot(self) -> AccountSnapshot: ...

    def submit_order(self, order: OrderIntent) -> str:
        """브로커 측 주문 참조 ID를 반환한다."""
        ...

    def cancel_order(self, order_id: str) -> None: ...

    def poll_fills(self, order_id: str) -> list[PaperFill]: ...
