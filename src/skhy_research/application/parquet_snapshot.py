"""정규화된 Bar를 Parquet snapshot으로 저장하고 manifest를 만든다 (P1-01, PRD 4.2).

DuckDB/연구 질의는 여기서 만든 manifest의 고정 파일 목록만 읽어 실행 중
데이터 유입으로 결과가 변하지 않게 한다. 원본 파일은 이후 계층에서 덮어쓰지
않으며, 재처리는 새 snapshot_id로 별도 저장한다.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from skhy_research.domain.market import Bar


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    record_count: int
    checksum: str


@dataclass(frozen=True)
class DataSnapshotManifest:
    snapshot_id: str
    dataset: str
    created_at_utc: int
    files: tuple[SnapshotFile, ...]
    total_record_count: int


def _bar_to_row(bar: Bar) -> dict[str, object]:
    return {
        "instrument_id": bar.instrument_id,
        "source": bar.source,
        "venue": bar.venue.value,
        "symbol": bar.symbol,
        "period": bar.period,
        "event_time_utc": bar.event_time_utc,
        "bar_close_time_utc": bar.bar_close_time_utc,
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "turnover": str(bar.turnover) if bar.turnover is not None else None,
        "currency": bar.currency.value if bar.currency else None,
        "is_adjusted": bar.is_adjusted,
        "adjustment_status": bar.adjustment_status.value,
        "construction_method": bar.construction.method,
        "construction_source_segment": bar.construction.source_segment,
        "quality_flag": ",".join(f.value for f in bar.quality_flag),
    }


class ParquetSnapshotWriter:
    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root

    def write(
        self, dataset: str, bars: list[Bar], snapshot_id: str | None = None
    ) -> DataSnapshotManifest:
        if not bars:
            raise ValueError("빈 bar 목록으로 snapshot을 만들 수 없다")
        sid = snapshot_id or str(uuid.uuid4())
        rows = [_bar_to_row(b) for b in bars]
        table = pa.Table.from_pylist(rows)

        out_dir = self._data_root / "normalized" / dataset / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / "bars.parquet"
        pq.write_table(table, file_path)
        checksum = hashlib.sha256(file_path.read_bytes()).hexdigest()

        manifest = DataSnapshotManifest(
            snapshot_id=sid,
            dataset=dataset,
            created_at_utc=time.time_ns(),
            files=(SnapshotFile(path=str(file_path), record_count=len(rows), checksum=checksum),),
            total_record_count=len(rows),
        )
        return manifest

    def read_manifest(self, manifest: DataSnapshotManifest) -> pa.Table:
        """manifest에 고정된 파일 목록만 읽는다. 이후 추가된 파일은 무시한다."""
        tables = [pq.read_table(f.path) for f in manifest.files]
        return pa.concat_tables(tables) if len(tables) > 1 else tables[0]
