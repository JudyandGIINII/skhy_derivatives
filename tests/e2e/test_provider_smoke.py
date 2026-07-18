"""P0-12: 사용자 키 주입 환경의 KRX/KIS/Toss 조회 전용 smoke test.

macOS Keychain 실행:
``SKHY_SECRET_BACKEND=keychain uv run pytest -m smoke tests/e2e/test_provider_smoke.py -v``

KIS_ACCOUNT_NO는 요구하지 않으며 계좌·주문 API를 호출하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from skhy_research.adapters.providers.krx import KrxReadOnlyClient
from skhy_research.adapters.providers.toss import TossReadOnlyClient
from skhy_research.adapters.secrets.factory import build_secret_provider
from skhy_research.application.live_capability_probe import (
    ReadOnlyProbeProvider,
    build_kis_read_only_probe_provider,
)
from skhy_research.domain.provider_capability import ReadOnlyProbeEvidence
from skhy_research.ports.secrets import SecretProvider

pytestmark = pytest.mark.smoke


def _require_secrets(provider: SecretProvider, names: tuple[str, ...]) -> None:
    missing = [name for name in names if not provider.get_secret(name)]
    if missing:
        pytest.skip(f"실 API 조회용 키 누락: {missing}")


@pytest.mark.parametrize(
    ("required_secrets", "build_client", "required_field"),
    [
        (("KRX_API_KEY",), KrxReadOnlyClient, "BAS_DD"),
        (
            ("KIS_APP_KEY", "KIS_APP_SECRET"),
            build_kis_read_only_probe_provider,
            "stck_prpr",
        ),
        (
            ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"),
            TossReadOnlyClient,
            "symbol",
        ),
    ],
    ids=("krx-daily-stock", "kis-domestic-quote", "toss-stock-info"),
)
def test_read_only_provider_live(
    required_secrets: tuple[str, ...],
    build_client: Callable[[SecretProvider], ReadOnlyProbeProvider],
    required_field: str,
) -> None:
    secret_provider = build_secret_provider()
    _require_secrets(secret_provider, required_secrets)
    client = build_client(secret_provider)
    try:
        evidence = client.probe_read_only()
    finally:
        client.close()

    assert isinstance(evidence, ReadOnlyProbeEvidence)
    assert evidence.record_count >= 1
    assert required_field in evidence.observed_fields
    assert evidence.measured_latency_ms > 0
