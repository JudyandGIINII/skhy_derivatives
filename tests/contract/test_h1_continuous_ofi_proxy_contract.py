"""sanitized KIS fixture가 실데이터 source gate로 승격되지 않는 계약."""

from __future__ import annotations

from decimal import Decimal

import pytest

from skhy_research.application.h1_continuous_ofi_proxy import (
    ProgramSemanticsSealEvidence,
    SourceGateEvidence,
    evaluate_source_gate,
)
from skhy_research.domain.enums import PromotionVerdict
from skhy_research.features.h1_close_pressure.continuous_ofi import FeatureDataOrigin
from tests._h1_shared_stream_support import load_h1_shared_fixture


@pytest.mark.contract
def test_sanitized_capture_cannot_pass_even_with_fixture_shaped_probe_fields() -> None:
    _, packets = load_h1_shared_fixture()
    assert packets
    evidence = SourceGateEvidence(
        data_origin=FeatureDataOrigin.SANITIZED_FIXTURE,
        consecutive_probe_days=2,
        all_five_raw_feeds_preserved=True,
        schema_parse_rate=Decimal("1"),
        field_count_match_rate=Decimal("1"),
        max_clock_error_ms=Decimal("0"),
        all_snapshot_ages_within_2s=True,
        depth_complete_rate=Decimal("1"),
        cutoff_depth_complete=True,
        quote_and_trade_max_gaps_within_2s=True,
        program_semantics=ProgramSemanticsSealEvidence(
            cumulative_buy_sell_monotonic=True,
            net_identity_exact=True,
            eod_three_way_reconciled=True,
            two_day_session_reset_verified=True,
            integrated_crosscheck_passed=True,
        ),
        venue_mapping_verified=True,
        causal_structural_inputs_verified=True,
        scheduled_operational_days=20,
        eligible_operational_days=19,
        raw_normalized_reconciliation_complete=True,
        program_window_all_eligible_days=True,
    )

    result = evaluate_source_gate(evidence)

    assert result.verdict is PromotionVerdict.HOLD
    assert result.reasons == ("LIVE_SOURCE_NOT_CAPTURED",)
