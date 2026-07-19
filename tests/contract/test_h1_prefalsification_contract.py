"""사전반증 JSON lineage와 fixture 비승격 계약."""

from __future__ import annotations

from pathlib import Path

import pytest

from skhy_research.application.h1_prefalsification_study import (
    PrefalsificationDataOrigin,
    PrefalsificationStatus,
    PrefalsificationVerdict,
    load_prefalsification_observations_json,
    run_prefalsification_study,
    run_weak_daily_prefalsification_study,
)

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "krx" / "h1_prefalsification_sanitized.json"


@pytest.mark.contract
def test_sanitized_json_preserves_sources_units_timestamps_and_cannot_decide() -> None:
    observations = load_prefalsification_observations_json(_FIXTURE)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.data_origin is PrefalsificationDataOrigin.SANITIZED_FIXTURE
    assert observation.program_net_buy_notional.source == "KRX_INFORMATION_PORTAL_12009"
    assert observation.program_net_buy_notional.unit == "KRW"
    reference_time = observation.pre_auction_reference_price.event_time_utc
    assert reference_time is not None
    assert reference_time < observation.auction_start_utc
    assert set(observation.control_returns) == {
        "kospi_return",
        "krx_semiconductor_return",
        "samsung_005930_return",
    }

    result = run_prefalsification_study(observations)
    assert result.status is PrefalsificationStatus.FIXTURE_ONLY
    assert result.verdict is PrefalsificationVerdict.HOLD
    assert result.order_submission_enabled is False


@pytest.mark.contract
def test_weak_daily_result_is_explicitly_labeled_and_asymmetric() -> None:
    result = run_weak_daily_prefalsification_study(())
    payload = result.to_dict()

    assert payload["model_variant"] == "weak_daily_v1"
    assert payload["status"] == "HOLD_DATA_UNAVAILABLE"
    assert payload["raw_model"] is None
    assert payload["controlled_model"] is None
    assert payload["order_submission_enabled"] is False
    warnings = payload["warnings"]
    assert isinstance(warnings, list)
    assert any("PROCEED_TO_LIVE" in warning and "약한 청신호" in warning for warning in warnings)
    assert any("FALSIFY" in warning and "false-negative" in warning for warning in warnings)
