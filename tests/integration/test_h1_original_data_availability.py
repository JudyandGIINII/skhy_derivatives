"""실데이터 없는 원 H1 검증은 성과를 조작하지 않고 HOLD로 차단한다."""

from __future__ import annotations

import pytest

from skhy_research.application.h1_original_validation import (
    assess_h1_original_data_availability,
    validate_stored_h1_original,
)
from skhy_research.domain.enums import PromotionVerdict


@pytest.mark.integration
def test_empty_normalized_catalog_is_g03_blocked_and_has_no_backtest(clean_pg) -> None:
    availability = assess_h1_original_data_availability(clean_pg)

    assert availability.can_run_real_validation is False
    assert "close_auction_imbalance" in availability.missing_requirements
    assert "program_net_buy" in availability.missing_requirements
    assert "trained_kappa_regime" in availability.missing_requirements
    assert availability.blocked_gate_ids == ("G-03",)

    result = validate_stored_h1_original(clean_pg)

    assert result.promotion.verdict is PromotionVerdict.HOLD
    assert result.promotion.promotion_eligible is False
    assert result.backtest is None
