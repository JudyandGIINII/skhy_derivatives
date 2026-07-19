"""수동 데이터가 부족한 실행은 산출물을 보존하고 HOLD로 닫힌."""

from __future__ import annotations

from datetime import date

from skhy_research.application.general_flow_study_runner import (
    execute_general_flow_study,
)
from skhy_research.features.g9_idiosyncratic_flow import InvestorFlowScope
from skhy_research.prefalsification.general_flow_study import (
    GeneralFlowStatus,
    GeneralFlowStudyConfig,
    GeneralFlowVerdict,
)


def test_partial_manual_backfill_is_persisted_but_study_is_honest_hold(tmp_path) -> None:
    market = tmp_path / "market.csv"
    market.write_text(
        "일자,기관 합계,외국인 합계,전체\n"
        "2026/07/15,-100,100,0\n"
        "2026/07/16,200,-200,0\n",
        encoding="utf-8",
    )

    execution = execute_general_flow_study(
        data_root=tmp_path / "data",
        price_snapshot_path=None,
        investor_csv_paths={InvestorFlowScope.MARKET: market},
        short_sale_csv_path=None,
        investor="외국인 합계",
        train_end=None,
        actual_product_listing_date=date(2026, 5, 27),
        fake_listing_dates=(date(2025, 5, 27),),
        config=GeneralFlowStudyConfig(permutations=10, bootstrap_resamples=20),
    )

    assert execution.study.status is GeneralFlowStatus.HOLD_DATA_UNAVAILABLE
    assert execution.study.verdict is GeneralFlowVerdict.HOLD
    assert execution.study.primary_model is None
    assert len(execution.backfill_artifacts) == 1
    assert execution.backfill_artifacts[0].record_count == 4
    assert "krx_investor_net_buy:000660" in execution.study.missing_datasets
    assert "krx_short_selling_comprehensive:MDCSTAT300:000660" in (
        execution.study.missing_datasets
    )
