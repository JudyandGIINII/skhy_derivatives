"""G9 OLS/HAC/permutation/bootstrap와 D3 판정 계약."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import numpy as np

from skhy_research.prefalsification.general_flow_study import (
    GENERAL_FLOW_WARNINGS,
    GeneralFlowRegressionRow,
    GeneralFlowStatus,
    GeneralFlowStudyConfig,
    GeneralFlowVerdict,
    NegativeControlKind,
    NegativeControlSpec,
    assess_general_flow_statistics,
    build_negative_control_specs,
    fit_general_flow_regression,
    run_general_flow_study,
    write_general_flow_reports,
)


def _config() -> GeneralFlowStudyConfig:
    return GeneralFlowStudyConfig(
        seed=19,
        permutations=120,
        bootstrap_resamples=120,
        bootstrap_block_days=10,
    )


def _rows(
    count: int,
    *,
    related: bool,
    symbol: str = "000660",
    seed: int = 1,
) -> tuple[GeneralFlowRegressionRow, ...]:
    rng = np.random.default_rng(seed)
    result: list[GeneralFlowRegressionRow] = []
    start = date(2020, 1, 2)
    for index in range(count):
        x = Decimal((index % 29) - 14) * Decimal("1000000000") + Decimal(index * 1_000_000)
        volume = Decimal(1000 + (index % 17) * 13 + index % 3)
        balance = Decimal(1_000_000 + (index % 31) * 101 + index * 7)
        noise = Decimal(str(float(rng.normal(0, 0.0003))))
        y = (
            x / Decimal("1000000000000") * Decimal("0.08")
            + volume / Decimal("100000000")
            - balance / Decimal("100000000000")
            + noise
            if related
            else noise
        )
        result.append(
            GeneralFlowRegressionRow(
                trading_date=start + timedelta(days=index),
                symbol=symbol,
                x_idio_nb_lag1=x,
                short_volume_lag1=volume,
                short_balance_lag2=balance,
                y_open_to_close_return=y,
                input_record_ids=(f"row:{symbol}:{index}",),
            )
        )
    return tuple(result)


def _negative_specs(count: int = 180) -> tuple[NegativeControlSpec, ...]:
    return (
        NegativeControlSpec(
            "pre_product",
            NegativeControlKind.PRE_PRODUCT_000660,
            _rows(count, related=False, seed=101),
        ),
        NegativeControlSpec(
            "peer_005930",
            NegativeControlKind.PEER_SEMICONDUCTOR,
            _rows(count, related=False, symbol="005930", seed=202),
        ),
        NegativeControlSpec(
            "fake_listing",
            NegativeControlKind.FAKE_LISTING_DATE,
            _rows(count, related=False, seed=303),
        ),
    )


def test_ols_hac_permutation_and_block_bootstrap_report_actual_statistics() -> None:
    statistics = fit_general_flow_regression(_rows(800, related=True), _config())
    assessment = assess_general_flow_statistics(statistics)

    assert statistics.observation_count == 800
    assert statistics.hac_standard_error > 0
    assert statistics.t_statistic > Decimal("1.96")
    assert statistics.permutation_p_value < Decimal("0.05")
    assert statistics.block_bootstrap_ci[0] > 0
    assert assessment.verdict is GeneralFlowVerdict.PROCEED


def test_all_three_negative_controls_must_be_clean_before_proceed() -> None:
    result = run_general_flow_study(
        _rows(800, related=True),
        scheduled_trading_days=800,
        negative_control_specs=_negative_specs(),
        config=_config(),
    )

    assert result.status is GeneralFlowStatus.COMPLETED
    assert result.verdict is GeneralFlowVerdict.PROCEED
    assert all(not item.false_signal_detected for item in result.negative_controls)
    assert result.warnings == GENERAL_FLOW_WARNINGS
    assert result.order_submission_enabled is False


def test_signal_in_peer_negative_control_falsifies_h1_interpretation() -> None:
    controls = list(_negative_specs())
    controls[1] = replace(controls[1], rows=_rows(180, related=True, symbol="005930"))

    result = run_general_flow_study(
        _rows(800, related=True),
        scheduled_trading_days=800,
        negative_control_specs=controls,
        config=_config(),
    )

    assert result.verdict is GeneralFlowVerdict.FALSIFY
    assert "D3_FALSE_SIGNAL:peer_005930" in result.reasons
    assert "FALSIFY≠리밸런싱 기각" in result.warnings


def test_less_than_756_days_is_hold_even_when_exploratory_stats_exist() -> None:
    result = run_general_flow_study(
        _rows(120, related=True),
        scheduled_trading_days=120,
        negative_control_specs=_negative_specs(80),
        config=replace(_config(), minimum_usable_observations=100),
    )

    assert result.status is GeneralFlowStatus.HOLD_SAMPLE_INSUFFICIENT
    assert result.verdict is GeneralFlowVerdict.HOLD
    assert result.primary_model is not None
    assert result.reasons == ("PRD_10_2_MINIMUM_756_TRADING_DAYS_NOT_MET",)


def test_missing_manual_datasets_is_honest_hold_without_model() -> None:
    result = run_general_flow_study(
        (),
        scheduled_trading_days=484,
        negative_control_specs=(),
        config=_config(),
        missing_datasets=("krx_investor_net_buy:000660", "MDCSTAT300:000660"),
    )

    assert result.status is GeneralFlowStatus.HOLD_DATA_UNAVAILABLE
    assert result.verdict is GeneralFlowVerdict.HOLD
    assert result.primary_model is None
    assert result.missing_datasets == (
        "krx_investor_net_buy:000660",
        "MDCSTAT300:000660",
    )


def test_negative_control_builder_repeats_pre_product_peer_and_fake_windows() -> None:
    primary = _rows(300, related=True)
    peer = _rows(300, related=False, symbol="005930")
    specs = build_negative_control_specs(
        primary,
        peer_rows={"005930": peer},
        actual_product_listing_date=date(2020, 7, 1),
        fake_listing_dates=(date(2020, 3, 1),),
    )

    assert {item.kind for item in specs} == set(NegativeControlKind)
    fake = next(item for item in specs if item.kind is NegativeControlKind.FAKE_LISTING_DATE)
    assert len(fake.rows) == 120
    assert fake.rows[0].trading_date >= date(2020, 3, 1)


def test_reports_force_three_interpretation_warnings(tmp_path) -> None:
    result = run_general_flow_study(
        _rows(120, related=True),
        scheduled_trading_days=120,
        negative_control_specs=_negative_specs(80),
        config=replace(_config(), minimum_usable_observations=100),
    )
    json_path, markdown_path = write_general_flow_reports(result, tmp_path)
    markdown = markdown_path.read_text(encoding="utf-8")
    payload = json_path.read_text(encoding="utf-8")

    for warning in GENERAL_FLOW_WARNINGS:
        assert warning in markdown
        assert warning in payload
