"""등록된 공급자의 capability를 조회해 probe report를 만든다 (P0-07, G-02).

fixture 환경에서는 이 결과가 CI의 계약 테스트 근거가 되고, 사용자 키 주입
환경(smoke)에서는 동일한 함수가 실제 capability catalog를 만든다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from skhy_research.application.provider_registry import ProviderRegistry
from skhy_research.domain.provider_capability import ProviderCatalogEntry


class ProbeResult(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    port_type: str
    provider_name: str
    ok: bool
    entry: ProviderCatalogEntry | None = None
    error: str | None = None


def run_capability_probe(registry: ProviderRegistry) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for port_type, name, provider in registry.iter_providers():
        try:
            entry = provider.capabilities()
            results.append(ProbeResult(port_type=port_type, provider_name=name, ok=True, entry=entry))
        except Exception as exc:  # noqa: BLE001 - probe는 어떤 실패든 결과로 남겨야 한다
            results.append(
                ProbeResult(port_type=port_type, provider_name=name, ok=False, error=str(exc))
            )
    return results
