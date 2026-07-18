"""P1-09 완료조건: 15:10 시점 H1 신호에 사후 공개 AUM/NAV가 섞이지 않았음을

raw -> normalized -> signal lineage로 감사한다.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path

import pytest

from skhy_research.adapters.persistence.manifest_store import (
    add_lineage_edge,
    trace_lineage_for_record,
)
from skhy_research.adapters.persistence.raw_recorder import RawRecorder, compute_dedupe_key
from skhy_research.domain.calendar import local_datetime_to_utc_nanos
from skhy_research.domain.enums import Venue
from skhy_research.domain.experiment import LineageEdge
from skhy_research.domain.reference import FundSnapshot
from skhy_research.features.h1_close_pressure.close_pressure import ClosePressureResult
from skhy_research.strategies.h1_close_rebalance.decision_window import build_decision_window
from skhy_research.strategies.h1_close_rebalance.lookahead_guard import LookaheadViolationError
from skhy_research.strategies.h1_close_rebalance.strategy import H1CloseRebalanceStrategy

_TRADING_DATE = date(2026, 3, 10)  # 임의의 화요일 근처 평일


def _snapshot_payload(published_at: int) -> dict:
    return {
        "source": "hkex_issuer",
        "venue": "HKEX",
        "symbol": "7709",
        "event_time_utc": published_at,
        "received_time_utc": published_at,
        "currency": "HKD",
        "session": "REFERENCE",
        "is_delayed": False,
        "adjustment_status": "NOT_APPLICABLE",
        "fund_id": "HKEX_7709",
        "leverage_beta": "2",
        "aum": "1000000",
        "nav": "10.5",
        "replication_type": "SWAP",
        "published_at": published_at,
        "effective_at": published_at,
    }


@pytest.mark.integration
def test_signal_lineage_traces_back_to_raw_when_no_lookahead(clean_pg, tmp_path: Path) -> None:
    """정상 케이스: 전일 공개된 NAV로 만든 신호는 raw까지 역추적되고 위반이 없다."""
    recorder = RawRecorder(clean_pg, tmp_path)
    window = build_decision_window(_TRADING_DATE, "15:10:00", "15:19:30")

    published_previous_day_utc = local_datetime_to_utc_nanos(
        date(2026, 3, 9), dtime(16, 0), Venue.HKEX
    )
    payload = _snapshot_payload(published_previous_day_utc)
    raw_bytes = json.dumps(payload).encode("utf-8")
    dedupe_key = compute_dedupe_key("hkex_issuer", "fund_snapshot", "snapshot", published_previous_day_utc, "n/a")

    stored = recorder.store(
        source="hkex_issuer",
        dataset="fund_snapshot",
        payload=raw_bytes,
        received_at_utc=published_previous_day_utc,
        collection_run_id="p1-09-audit",
        dedupe_key=dedupe_key,
    )
    fund_snapshot = FundSnapshot(**payload)

    strategy = H1CloseRebalanceStrategy(strategy_version="1.0.0", neutral_band=Decimal("0.001"))
    signal_id = str(uuid.uuid4())
    decision = strategy.decide(
        instrument_id="SKHY_000660_KRX_COMMON",
        feature_set_id="h1_close_pressure@1.0.0",
        close_pressure=ClosePressureResult(Decimal("0.004"), "full", ()),
        input_record_ids=[stored.meta.raw_record_id],
        fund_snapshots_used=[fund_snapshot],
        decision_time_utc=window.signal_snapshot_utc,
        expires_at_utc=window.order_intent_cutoff_utc,
        signal_id=signal_id,
        estimated_cost=Decimal("0.001"),
    )
    assert decision.signal is not None

    normalized_record_id = str(uuid.uuid4())
    now = time.time_ns()
    add_lineage_edge(
        clean_pg,
        LineageEdge(
            edge_id=str(uuid.uuid4()),
            run_id="p1-09-audit",
            parent_record_id=stored.meta.raw_record_id,
            parent_layer="raw",
            child_record_id=normalized_record_id,
            child_layer="normalized",
            algorithm_version="fund_snapshot_normalizer@1.0.0",
            created_at_utc=now,
        ),
    )
    add_lineage_edge(
        clean_pg,
        LineageEdge(
            edge_id=str(uuid.uuid4()),
            run_id="p1-09-audit",
            parent_record_id=normalized_record_id,
            parent_layer="normalized",
            child_record_id=signal_id,
            child_layer="signal",
            algorithm_version="h1_close_rebalance@1.0.0",
            created_at_utc=now,
        ),
    )

    edges = trace_lineage_for_record(clean_pg, "p1-09-audit", signal_id)
    parents = {e.parent_record_id for e in edges}
    assert stored.meta.raw_record_id in parents
    assert normalized_record_id in parents


def test_same_day_post_close_nav_is_rejected_before_any_signal_or_lineage_is_created() -> None:
    """위반 케이스: 당일 장후 확정 NAV는 신호도, lineage edge도 만들어지지 않는다."""
    window = build_decision_window(_TRADING_DATE, "15:10:00", "15:19:30")
    same_day_post_close_utc = local_datetime_to_utc_nanos(_TRADING_DATE, dtime(16, 0), Venue.HKEX)
    fund_snapshot = FundSnapshot(**_snapshot_payload(same_day_post_close_utc))

    strategy = H1CloseRebalanceStrategy(strategy_version="1.0.0", neutral_band=Decimal("0.001"))

    with pytest.raises(LookaheadViolationError):
        strategy.decide(
            instrument_id="SKHY_000660_KRX_COMMON",
            feature_set_id="h1_close_pressure@1.0.0",
            close_pressure=ClosePressureResult(Decimal("0.004"), "full", ()),
            input_record_ids=[],
            fund_snapshots_used=[fund_snapshot],
            decision_time_utc=window.signal_snapshot_utc,
            expires_at_utc=window.order_intent_cutoff_utc,
            signal_id=str(uuid.uuid4()),
            estimated_cost=Decimal("0.001"),
        )
