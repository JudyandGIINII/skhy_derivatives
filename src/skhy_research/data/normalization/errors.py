"""정규화 실패 예외. raw 레코드를 조용히 버리지 않고 사유와 함께 보고한다."""

from __future__ import annotations


class NormalizationError(RuntimeError):
    def __init__(
        self, source: str, dataset: str, reason: str, raw_record_id: str | None = None
    ) -> None:
        suffix = f" (raw_record_id={raw_record_id})" if raw_record_id else ""
        super().__init__(f"[{source}/{dataset}] 정규화 실패: {reason}{suffix}")
        self.source = source
        self.dataset = dataset
        self.reason = reason
        self.raw_record_id = raw_record_id
