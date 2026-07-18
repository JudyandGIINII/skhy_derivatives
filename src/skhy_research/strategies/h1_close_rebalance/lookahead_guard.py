"""룩어헤드 차단 (FR-09): 당일 장후 확정 AUM/NAV가 같은 날 신호에 섞이지 않는다."""

from __future__ import annotations

from skhy_research.domain.reference import FundSnapshot


class LookaheadViolationError(RuntimeError):
    """신호 생성 시점 이후에 공개된(published_at >= decision_time) 데이터가 입력에 섞였을 때."""


def assert_no_lookahead(fund_snapshots: list[FundSnapshot], decision_time_utc: int) -> None:
    for snapshot in fund_snapshots:
        if snapshot.published_at >= decision_time_utc:
            raise LookaheadViolationError(
                f"fund_id={snapshot.fund_id}의 published_at({snapshot.published_at})이 "
                f"decision_time({decision_time_utc}) 이후이거나 같다 — "
                "당일 장후 확정치가 섞였을 수 있다"
            )
