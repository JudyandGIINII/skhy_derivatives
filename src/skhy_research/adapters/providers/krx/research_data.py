"""KRX Open API의 일반수급·공매도 제공 범위 계약.

공식 Open API 카탈로그에 없는 Data Marketplace 화면 항목을 숨겨진
엔드포인트로 추측하지 않는다. 미제공 데이터는 사용자가 공식
Data Marketplace에서 수동 다운로드한 CSV로만 적재한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

OFFICIAL_OPEN_API_STOCK_CATALOG_URL = (
    "https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S1.cmd"
)
OFFICIAL_CATALOG_VERIFIED_ON = date(2026, 7, 19)


class KrxResearchDataset(StrEnum):
    STOCK_DAILY = "stk_bydd_trd"
    INVESTOR_NET_BUY = "investor_net_buy_notional"
    SHORT_SELLING_COMPREHENSIVE = "MDCSTAT300"


class KrxOpenApiProvisionStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_IN_OFFICIAL_CATALOG = "NOT_IN_OFFICIAL_KRX_OPEN_API_CATALOG"


@dataclass(frozen=True)
class KrxResearchDatasetAvailability:
    dataset: KrxResearchDataset
    status: KrxOpenApiProvisionStatus
    evidence_url: str
    verified_on: date
    permitted_collection_path: str


RESEARCH_DATASET_AVAILABILITY = (
    KrxResearchDatasetAvailability(
        dataset=KrxResearchDataset.STOCK_DAILY,
        status=KrxOpenApiProvisionStatus.AVAILABLE,
        evidence_url=OFFICIAL_OPEN_API_STOCK_CATALOG_URL,
        verified_on=OFFICIAL_CATALOG_VERIFIED_ON,
        permitted_collection_path="KRX_OPEN_API:/svc/apis/sto/stk_bydd_trd",
    ),
    KrxResearchDatasetAvailability(
        dataset=KrxResearchDataset.INVESTOR_NET_BUY,
        status=KrxOpenApiProvisionStatus.NOT_IN_OFFICIAL_CATALOG,
        evidence_url=OFFICIAL_OPEN_API_STOCK_CATALOG_URL,
        verified_on=OFFICIAL_CATALOG_VERIFIED_ON,
        permitted_collection_path="KRX_DATA_MARKETPLACE_MANUAL_CSV_ONLY",
    ),
    KrxResearchDatasetAvailability(
        dataset=KrxResearchDataset.SHORT_SELLING_COMPREHENSIVE,
        status=KrxOpenApiProvisionStatus.NOT_IN_OFFICIAL_CATALOG,
        evidence_url=OFFICIAL_OPEN_API_STOCK_CATALOG_URL,
        verified_on=OFFICIAL_CATALOG_VERIFIED_ON,
        permitted_collection_path="KRX_DATA_MARKETPLACE_MANUAL_CSV:[MDCSTAT300]",
    ),
)


class KrxOpenApiDatasetUnavailableError(RuntimeError):
    """공식 Open API 카탈로그 미제공 항목의 추측 호출을 차단한다."""

    def __init__(self, dataset: KrxResearchDataset) -> None:
        self.dataset = dataset
        super().__init__(
            f"{dataset.value}은(는) 공식 KRX Open API 카탈로그에서 미제공된다. "
            "Data Marketplace 수동 CSV 적재 경로를 사용해야 한다."
        )


def research_dataset_availability() -> tuple[KrxResearchDatasetAvailability, ...]:
    return RESEARCH_DATASET_AVAILABILITY


def reject_unlisted_open_api_dataset(dataset: KrxResearchDataset) -> None:
    status = next(item for item in RESEARCH_DATASET_AVAILABILITY if item.dataset is dataset)
    if status.status is not KrxOpenApiProvisionStatus.AVAILABLE:
        raise KrxOpenApiDatasetUnavailableError(dataset)
