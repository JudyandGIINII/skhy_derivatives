"""주문 기능 없이 KIS/Toss H1 snapshot feed만 조립하는 factory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

from skhy_research.adapters.providers.kis import (
    KisEnvironment,
    KisReadOnlyClient,
    KisSnapshotMarketDataProvider,
)
from skhy_research.adapters.providers.toss import (
    TossReadOnlyClient,
    TossSnapshotMarketDataProvider,
)
from skhy_research.ports.secrets import SecretProvider


@dataclass(frozen=True)
class H1LiveFeedBundle:
    primary: KisSnapshotMarketDataProvider
    crosscheck: TossSnapshotMarketDataProvider
    _kis_client: KisReadOnlyClient
    _toss_client: TossReadOnlyClient

    def close(self) -> None:
        self._kis_client.close()
        self._toss_client.close()


def build_h1_live_feed_bundle(
    secret_provider: SecretProvider,
    *,
    kis_environment: KisEnvironment | None = None,
) -> H1LiveFeedBundle:
    """HTTP GET snapshot client만 만들며 broker·주문 port는 조립하지 않는다."""

    environment = kis_environment or _kis_environment()
    kis_client = KisReadOnlyClient(secret_provider, environment=environment)
    toss_client = TossReadOnlyClient(secret_provider)
    return H1LiveFeedBundle(
        primary=KisSnapshotMarketDataProvider(kis_client),
        crosscheck=TossSnapshotMarketDataProvider(toss_client),
        _kis_client=kis_client,
        _toss_client=toss_client,
    )


def _kis_environment() -> KisEnvironment:
    value = os.environ.get("KIS_ENV", "vps")
    if value not in {"vps", "prod"}:
        raise ValueError("KIS_ENV는 'vps' 또는 'prod'여야 한다")
    return cast(KisEnvironment, value)
