"""국내 단일종목 레버리지 상품과 HKEX 7709의 AUM/NAV/PCF 수집 (P1-02, FR-03/04/05/09).

`InstrumentMaster`에서 레버리지 자산군을 동적으로 찾아(정적 목록 고정 금지,
PRD 6장) `FundSnapshot`을 수집한다. 공개시각(published_at)이 없거나 조회
자체가 실패한 상품은 조용히 건너뛰지 않고 사유와 함께 `FundSnapshotExclusion`으로
별도 기록한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from skhy_research.application.instrument_master import InstrumentMaster
from skhy_research.domain.reference import FundSnapshot
from skhy_research.ports.errors import UnsupportedCapabilityError
from skhy_research.ports.reference_data import ReferenceDataProvider

LEVERAGED_ASSET_CLASSES = frozenset({"LEVERAGED_ETF", "LEVERAGED_ETN", "SWAP_PRODUCT"})


def discover_leveraged_products(master: InstrumentMaster, as_of_utc: int) -> list[str]:
    """레버리지 자산군이면서 as_of_utc 시점에 활성 상태인 instrument_id 목록."""
    return [
        record.instrument_id
        for record in master.list_instruments()
        if record.asset_class in LEVERAGED_ASSET_CLASSES
        and master.is_active_as_of(record.instrument_id, as_of_utc)
    ]


@dataclass(frozen=True)
class FundSnapshotExclusion:
    fund_id: str
    reason: str


@dataclass(frozen=True)
class FundSnapshotCollectionResult:
    snapshots: tuple[FundSnapshot, ...]
    exclusions: tuple[FundSnapshotExclusion, ...]


def collect_fund_snapshots(
    provider: ReferenceDataProvider, fund_ids: list[str]
) -> FundSnapshotCollectionResult:
    snapshots: list[FundSnapshot] = []
    exclusions: list[FundSnapshotExclusion] = []
    for fund_id in fund_ids:
        try:
            snapshot = provider.get_fund_snapshot(fund_id)
        except UnsupportedCapabilityError as exc:
            exclusions.append(FundSnapshotExclusion(fund_id, f"capability 미지원: {exc}"))
            continue
        except KeyError as exc:
            exclusions.append(FundSnapshotExclusion(fund_id, f"조회 실패(데이터 없음): {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001 - 정규화 실패(공개시각 누락 등)를 조용히 삼키지 않는다
            exclusions.append(FundSnapshotExclusion(fund_id, f"수집 실패: {exc}"))
            continue
        snapshots.append(snapshot)
    return FundSnapshotCollectionResult(tuple(snapshots), tuple(exclusions))
