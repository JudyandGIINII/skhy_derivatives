"""사용자 키가 주입된 환경에서 실행하는 조회 전용 capability probe."""

from __future__ import annotations

import os
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict

from skhy_research.adapters.providers.kis import KisEnvironment, KisReadOnlyClient
from skhy_research.adapters.providers.krx import KrxReadOnlyClient
from skhy_research.adapters.providers.toss import TossReadOnlyClient
from skhy_research.domain.provider_capability import (
    HealthStatus,
    ProviderCatalogEntry,
    ReadOnlyProbeEvidence,
)
from skhy_research.observability.masking import mask_exception
from skhy_research.ports.secrets import SecretProvider


class ReadOnlyProbeProvider(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def probe_read_only(self) -> ReadOnlyProbeEvidence: ...

    def close(self) -> None: ...


class LiveProbeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_name: str
    ok: bool
    catalog_entry: ProviderCatalogEntry
    evidence: ReadOnlyProbeEvidence | None = None
    error: str | None = None


def build_read_only_probe_providers(
    secret_provider: SecretProvider,
) -> tuple[ReadOnlyProbeProvider, ...]:
    """실주문 경로 없이 KRX/KIS/Toss 조회 어댑터만 생성한다."""
    return (
        KrxReadOnlyClient(secret_provider),
        build_kis_read_only_probe_provider(secret_provider),
        TossReadOnlyClient(secret_provider),
    )


def build_kis_read_only_probe_provider(secret_provider: SecretProvider) -> KisReadOnlyClient:
    return KisReadOnlyClient(secret_provider, environment=_kis_environment())


def run_live_capability_probe(
    providers: tuple[ReadOnlyProbeProvider, ...],
) -> list[LiveProbeResult]:
    results: list[LiveProbeResult] = []
    for provider in providers:
        entry = provider.capabilities()
        try:
            evidence = provider.probe_read_only()
            results.append(
                LiveProbeResult(
                    provider_name=entry.provider_name,
                    ok=True,
                    catalog_entry=entry.model_copy(
                        update={
                            "measured_latency_ms": evidence.measured_latency_ms,
                            "health_status": HealthStatus.HEALTHY,
                        }
                    ),
                    evidence=evidence,
                )
            )
        except Exception as exc:  # noqa: BLE001 - probe는 실패도 결과로 반환한다
            results.append(
                LiveProbeResult(
                    provider_name=entry.provider_name,
                    ok=False,
                    catalog_entry=entry.model_copy(update={"health_status": HealthStatus.DOWN}),
                    error=mask_exception(exc),
                )
            )
    return results


def close_probe_providers(providers: tuple[ReadOnlyProbeProvider, ...]) -> None:
    for provider in providers:
        provider.close()


def _kis_environment() -> KisEnvironment:
    value = os.environ.get("KIS_ENV", "vps")
    if value not in {"vps", "prod"}:
        raise ValueError("KIS_ENV는 'vps' 또는 'prod'여야 한다")
    return cast(KisEnvironment, value)
