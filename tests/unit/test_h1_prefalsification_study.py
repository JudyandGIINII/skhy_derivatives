"""H1 일별 사전반증 회귀의 인과·통계·HOLD 계약."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from skhy_research.application.h1_prefalsification_study import (
    CONTROL_FACTOR_NAMES,
    PREFALSIFICATION_FIELD_SPECIFICATIONS,
    WEAK_DAILY_FIELD_SPECIFICATIONS,
    DataAvailabilityAudit,
    PrefalsificationDailyObservation,
    PrefalsificationDataOrigin,
    PrefalsificationModelVariant,
    PrefalsificationStatus,
    PrefalsificationStudyConfig,
    PrefalsificationVerdict,
    RegressionStatistics,
    RegressionVariant,
    TimedStudyValue,
    WeakDailyObservation,
    assess_prefalsification_statistics,
    audit_existing_krx_daily_data,
    build_data_unavailable_result,
    build_prefalsification_rows,
    build_weak_daily_rows,
    collect_krx_weak_daily_inputs,
    fit_prefalsification_regression,
    load_krx_12009_program_net_buy,
    run_prefalsification_study,
    run_weak_daily_prefalsification_study,
    write_prefalsification_reports,
)
from skhy_research.ports.errors import ProviderAuthenticationError

_DAY_NS = 86_400 * 1_000_000_000
_SECOND_NS = 1_000_000_000


def _value(
    value: Decimal,
    *,
    event: int,
    available: int,
    source: str,
    unit: str,
    record_id: str,
) -> TimedStudyValue:
    return TimedStudyValue(
        value=value,
        event_time_utc=event,
        available_at_utc=available,
        source=source,
        unit=unit,
        input_record_id=record_id,
    )


def _program_signal(index: int) -> Decimal:
    return Decimal((index % 11) - 5) * Decimal("100000000")


def _observations(
    count: int = 80,
    *,
    origin: PrefalsificationDataOrigin = PrefalsificationDataOrigin.SANITIZED_FIXTURE,
    related: bool = True,
) -> tuple[PrefalsificationDailyObservation, ...]:
    result: list[PrefalsificationDailyObservation] = []
    start_date = date(2022, 1, 3)
    for index in range(count):
        day_start = 1_700_000_000_000_000_000 + index * _DAY_NS
        auction_start = day_start + 15 * 3_600 * _SECOND_NS + 20 * 60 * _SECOND_NS
        auction_end = auction_start + 10 * 60 * _SECOND_NS
        previous_signal = _program_signal(max(index - 1, 0))
        direction = (
            (Decimal("1") if previous_signal > 0 else Decimal("-1"))
            if related and previous_signal != 0
            else Decimal("1") if index % 2 == 0 else Decimal("-1")
        )
        reference = Decimal("100000")
        close = reference + direction * Decimal("100")
        if related:
            auction_notional = (
                abs(previous_signal) * Decimal("2")
                + Decimal((index % 5) + 1) * Decimal("10000000")
            )
        else:
            auction_notional = Decimal((index % 7) + 1) * Decimal("50000000")
        controls = {
            CONTROL_FACTOR_NAMES[0]: Decimal((index % 7) - 3) / Decimal("100"),
            CONTROL_FACTOR_NAMES[1]: Decimal((index % 11) - 5) / Decimal("100"),
            CONTROL_FACTOR_NAMES[2]: Decimal((index % 13) - 6) / Decimal("100"),
        }
        result.append(
            PrefalsificationDailyObservation(
                trading_date=start_date + timedelta(days=index),
                symbol="000660",
                auction_start_utc=auction_start,
                auction_end_utc=auction_end,
                program_net_buy_notional=_value(
                    _program_signal(index),
                    event=auction_end,
                    available=auction_end,
                    source="KRX_INFORMATION_PORTAL_12009",
                    unit="KRW",
                    record_id=f"program-{index}",
                ),
                pre_auction_reference_price=_value(
                    reference,
                    event=auction_start - _SECOND_NS,
                    available=auction_start - _SECOND_NS // 2,
                    source="KRX_PRE_AUCTION_REFERENCE",
                    unit="KRW",
                    record_id=f"reference-{index}",
                ),
                official_close_price=_value(
                    close,
                    event=auction_end,
                    available=auction_end,
                    source="KRX_OFFICIAL_CLOSE",
                    unit="KRW",
                    record_id=f"close-{index}",
                ),
                close_auction_turnover_notional=_value(
                    auction_notional,
                    event=auction_end,
                    available=auction_end,
                    source="KRX_CLOSE_AUCTION_DAILY",
                    unit="KRW",
                    record_id=f"auction-{index}",
                ),
                total_turnover_notional=_value(
                    Decimal("10000000000"),
                    event=auction_end,
                    available=auction_end,
                    source="KRX_OPEN_API_STK_BYDD_TRD",
                    unit="KRW",
                    record_id=f"turnover-{index}",
                ),
                control_returns={
                    name: _value(
                        value,
                        event=auction_end,
                        available=auction_end,
                        source=f"KRX_FACTOR:{name}",
                        unit="RETURN",
                        record_id=f"factor-{name}-{index}",
                    )
                    for name, value in controls.items()
                },
                data_origin=origin,
            )
        )
    return tuple(result)


def _fast_config() -> PrefalsificationStudyConfig:
    return PrefalsificationStudyConfig(
        seed=17,
        permutations=200,
        bootstrap_resamples=200,
        bootstrap_block_days=10,
    )


def _weak_observations(
    count: int = 80,
    *,
    origin: PrefalsificationDataOrigin = PrefalsificationDataOrigin.SANITIZED_FIXTURE,
    missing_program: bool = False,
) -> tuple[WeakDailyObservation, ...]:
    result: list[WeakDailyObservation] = []
    for item in _observations(count, origin=origin):
        market_open = item.auction_start_utc - (6 * 3_600 + 20 * 60) * _SECOND_NS
        program = item.program_net_buy_notional
        if missing_program:
            program = TimedStudyValue(
                value=None,
                event_time_utc=None,
                available_at_utc=None,
                source="KRX_OPEN_API_CATALOG_AUDIT",
                unit="KRW",
                input_record_id=None,
                missing_reason="PROGRAM_12009_NOT_IN_KRX_OPEN_API_CATALOG",
            )
        opening_value = item.pre_auction_reference_price.value
        assert opening_value is not None
        result.append(
            WeakDailyObservation(
                trading_date=item.trading_date,
                symbol=item.symbol,
                market_open_utc=market_open,
                official_close_utc=item.auction_end_utc,
                program_net_buy_notional=program,
                official_open_price=_value(
                    opening_value,
                    event=market_open,
                    available=item.auction_end_utc,
                    source="KRX_OPEN_API:stk_bydd_trd:TDD_OPNPRC",
                    unit="KRW",
                    record_id=f"weak-open-{item.trading_date}",
                ),
                official_close_price=item.official_close_price,
                total_turnover_notional=item.total_turnover_notional,
                control_returns=item.control_returns,
                data_origin=origin,
            )
        )
    return tuple(result)


def test_spec_seals_lagged_program_units_timing_and_no_daily_fallback() -> None:
    by_name = {item.name: item for item in PREFALSIFICATION_FIELD_SPECIFICATIONS}

    x_spec = by_name["x_program_lag1_adv20"]
    y_spec = by_name["y_signed_close_auction_notional_adv20"]
    assert "t-1" in x_spec.transformation
    assert x_spec.raw_unit == "KRW"
    assert "당일 종일 누적 프로그램 값은 금지" in x_spec.lookahead_rule
    assert "전일종가·시가" in y_spec.lookahead_rule
    assert "ADV20" in y_spec.transformation


def test_rows_use_only_prior_twenty_day_adv_and_lag_one_program() -> None:
    observations = _observations(22)
    build = build_prefalsification_rows(observations)

    assert build.raw_eligible_count == 2
    first = build.rows[0]
    expected_program = observations[19].program_net_buy_notional.value
    assert expected_program is not None
    assert first.x_program_lag1_adv20 == expected_program / Decimal("10000000000")
    assert first.adv20_notional == Decimal("10000000000")
    assert "program-19" in first.input_record_ids
    assert "turnover-20" not in first.input_record_ids
    assert first.control_returns is not None


def test_post_cutoff_lagged_program_is_missing_not_zero() -> None:
    observations = list(_observations(22))
    previous = observations[19]
    late_program = replace(
        previous.program_net_buy_notional,
        available_at_utc=observations[20].auction_start_utc + 1,
    )
    observations[19] = replace(previous, program_net_buy_notional=late_program)

    build = build_prefalsification_rows(observations)

    assert build.raw_eligible_count == 1
    assert build.missing_reason_counts["PROGRAM_LAG1_POST_CUTOFF"] == 1


def test_ols_hac_permutation_bootstrap_and_effect_size_are_reported() -> None:
    rows = build_prefalsification_rows(_observations()).rows
    raw = fit_prefalsification_regression(
        rows, variant=RegressionVariant.RAW, config=_fast_config()
    )
    controlled = fit_prefalsification_regression(
        rows,
        variant=RegressionVariant.COMMON_FACTOR_RESIDUAL,
        config=_fast_config(),
    )

    for statistics in (raw, controlled):
        assert statistics.observation_count == 60
        assert statistics.hac_max_lags >= 1
        assert statistics.hac_standard_error > 0
        assert statistics.beta > 0
        assert statistics.block_bootstrap_ci[0] < statistics.block_bootstrap_ci[1]
        assert Decimal("0") <= statistics.permutation_p_value <= Decimal("1")
        assert statistics.standardized_effect_size > 0
    assert controlled.control_names == CONTROL_FACTOR_NAMES


def test_each_failure_condition_independently_falsifies() -> None:
    base = RegressionStatistics(
        variant=RegressionVariant.RAW,
        observation_count=756,
        hac_max_lags=5,
        intercept=Decimal("0"),
        beta=Decimal("1"),
        hac_standard_error=Decimal("0.1"),
        t_statistic=Decimal("10"),
        analytic_two_sided_p=Decimal("0.001"),
        permutation_p_value=Decimal("0.01"),
        block_bootstrap_ci=(Decimal("0.5"), Decimal("1.5")),
        standardized_effect_size=Decimal("0.2"),
        r_squared=Decimal("0.1"),
        control_names=(),
    )
    cases = (
        replace(base, t_statistic=Decimal("1.96")),
        replace(base, block_bootstrap_ci=(Decimal("-0.1"), Decimal("1"))),
        replace(base, permutation_p_value=Decimal("0.05")),
        replace(base, beta=Decimal("-1"), t_statistic=Decimal("-10")),
    )

    assert assess_prefalsification_statistics(base).verdict is PrefalsificationVerdict.PROCEED_TO_LIVE
    assert all(
        assess_prefalsification_statistics(case).verdict
        is PrefalsificationVerdict.FALSIFY
        for case in cases
    )


def test_fixture_can_exercise_models_but_never_authorize_live_collection() -> None:
    result = run_prefalsification_study(_observations(), _fast_config())

    assert result.status is PrefalsificationStatus.FIXTURE_ONLY
    assert result.verdict is PrefalsificationVerdict.HOLD
    assert result.raw_model is not None
    assert result.controlled_model is not None
    assert result.order_submission_enabled is False
    assert result.reasons == ("SANITIZED_FIXTURE_NOT_DECISION_ELIGIBLE",)


def test_actual_but_short_sample_is_hold_under_prd_10_2() -> None:
    actual = _observations(
        origin=PrefalsificationDataOrigin.KRX_HISTORICAL_ACTUAL
    )
    result = run_prefalsification_study(actual, _fast_config())

    assert result.status is PrefalsificationStatus.HOLD_SAMPLE_INSUFFICIENT
    assert result.verdict is PrefalsificationVerdict.HOLD
    assert result.reasons == ("PRD_10_2_MINIMUM_3Y_SAMPLE_NOT_MET",)


def test_existing_ohlcv_without_program_and_auction_is_explicit_hold(
    tmp_path,
) -> None:
    parquet_dir = tmp_path / "normalized" / "krx_daily_ohlcv" / "snapshot"
    parquet_dir.mkdir(parents=True)
    pq.write_table(
        pa.table({"symbol": ["000660", "005930"]}), parquet_dir / "bars.parquet"
    )

    audit = audit_existing_krx_daily_data(tmp_path)
    result = build_data_unavailable_result(audit)

    assert isinstance(audit, DataAvailabilityAudit)
    assert audit.ohlcv_bar_count == 2
    assert audit.ohlcv_symbols == ("000660", "005930")
    assert "krx_program_trading_daily_12009" in audit.missing_required_datasets
    assert "krx_close_auction_daily" in audit.missing_required_datasets
    assert result.status is PrefalsificationStatus.HOLD_DATA_UNAVAILABLE
    assert result.verdict is PrefalsificationVerdict.HOLD
    assert result.raw_model is None


def test_json_and_markdown_reports_are_written_with_scope_and_statistics(tmp_path) -> None:
    result = run_prefalsification_study(_observations(), _fast_config())
    json_path, markdown_path = write_prefalsification_reports(result, tmp_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["promotion_scope"] == "live-collection-go-no-go-only"
    assert payload["order_submission_enabled"] is False
    assert payload["raw_model"]["hac_standard_error"]
    assert "라이브 수집 착수 여부" in markdown
    assert "주문 제출: 비활성화" in markdown
    assert "block bootstrap 95% CI" in markdown


def test_weak_daily_v1_seals_open_to_close_y_and_keeps_lagged_x() -> None:
    by_name = {item.name: item for item in WEAK_DAILY_FIELD_SPECIFICATIONS}
    build = build_weak_daily_rows(_weak_observations(22))

    assert "TDD_CLSPRC(t) / TDD_OPNPRC(t)" in by_name[
        "y_weak_open_to_close_return"
    ].transformation
    assert "1거래일 시차" in by_name["x_program_lag1_adv20"].lookahead_rule
    assert build.raw_eligible_count == 2
    expected = Decimal(str(math.log(100100 / 100000)))
    assert build.rows[0].y_signed_close_auction_notional_adv20 == expected
    assert "program-19" in build.rows[0].input_record_ids


def test_weak_daily_fixture_is_labeled_warned_and_never_decides() -> None:
    result = run_weak_daily_prefalsification_study(
        _weak_observations(), _fast_config()
    )

    assert result.model_variant is PrefalsificationModelVariant.WEAK_DAILY_V1
    assert result.status is PrefalsificationStatus.FIXTURE_ONLY
    assert result.verdict is PrefalsificationVerdict.HOLD
    assert result.raw_model is not None
    assert any("FALSIFY" in warning and "false-negative" in warning for warning in result.warnings)


def test_weak_daily_real_three_year_shape_with_missing_program_is_explicit_hold() -> None:
    result = run_weak_daily_prefalsification_study(
        _weak_observations(
            origin=PrefalsificationDataOrigin.KRX_HISTORICAL_ACTUAL,
            missing_program=True,
        ),
        _fast_config(),
    )

    assert result.status is PrefalsificationStatus.HOLD_DATA_UNAVAILABLE
    assert result.raw_model is None
    assert result.controlled_model is None
    assert result.missing_reason_counts[
        "PROGRAM_12009_NOT_IN_KRX_OPEN_API_CATALOG"
    ] == 60


class _WeakCollectionClient:
    def fetch_daily_krx_index_trades(
        self, trading_date: date
    ) -> list[dict[str, Any]]:
        raise ProviderAuthenticationError("krx", "not-entitled")

    def fetch_daily_stock_trades(
        self, trading_date: date
    ) -> list[dict[str, Any]]:
        basis = trading_date.strftime("%Y%m%d")
        return [
            {
                "BAS_DD": basis,
                "ISU_CD": "000660",
                "TDD_OPNPRC": "100000",
                "TDD_CLSPRC": "101000",
                "ACC_TRDVAL": "10000000000",
            },
            {
                "BAS_DD": basis,
                "ISU_CD": "005930",
                "TDD_OPNPRC": "70000",
                "TDD_CLSPRC": "70700",
                "ACC_TRDVAL": "9000000000",
            },
        ]

    def fetch_daily_kospi_index_trades(
        self, trading_date: date
    ) -> list[dict[str, Any]]:
        return [
            {
                "BAS_DD": trading_date.strftime("%Y%m%d"),
                "IDX_NM": "코스피",
                "OPNPRC_IDX": "3000",
                "CLSPRC_IDX": "3030",
            }
        ]


def test_weak_collection_preserves_missing_program_and_endpoint_evidence(tmp_path) -> None:
    snapshot_path = tmp_path / "weak_inputs.json"
    collection = collect_krx_weak_daily_inputs(
        _WeakCollectionClient(),
        end=date(2026, 7, 17),
        output_path=snapshot_path,
        minimum_trading_days=2,
        max_lookback_calendar_days=5,
        min_request_interval_seconds=0,
    )

    assert len(collection.observations) == 2
    assert collection.observations[0].program_net_buy_notional.value is None
    assert collection.observations[0].program_net_buy_notional.missing_reason == (
        "PROGRAM_12009_NOT_IN_KRX_OPEN_API_CATALOG"
    )
    audit = collection.availability_audit
    assert audit.dataset_coverage["krx_daily_ohlcv"] == 2
    assert "krx_program_trading_daily_12009" in audit.missing_required_datasets
    assert audit.endpoint_status["krx_semiconductor_index_daily"] == (
        "AUTHENTICATED_KEY_NOT_ENTITLED_TO_KRX_INDEX_ENDPOINT"
    )
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["program_api_catalog_status"] == (
        "NOT_IN_OFFICIAL_KRX_OPEN_API_CATALOG"
    )
    assert all(row["program_net_buy_notional"] is None for row in payload["observations"])


_KRX_12009_HEADER = (
    "일자,종목코드,종목명,"
    "차익_순매수_거래대금,비차익_순매수_거래대금,"
    "전체_순매수_거래량,전체_순매수_거래대금"
)


def _write_program_csv(path, rows: list[str], *, header: str = _KRX_12009_HEADER):
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def test_program_csv_loader_detects_total_net_buy_and_preserves_sign(tmp_path) -> None:
    csv_path = _write_program_csv(
        tmp_path / "krx12009.csv",
        [
            '2026/07/17,000660,SK하이닉스,1000000,2000000,120,"3,000,000"',
            '2026/07/16,000660,SK하이닉스,-500000,-250000,-30,"-750,000"',
            "2026/07/15,005930,삼성전자,9,9,9,9999",
        ],
    )

    load = load_krx_12009_program_net_buy(csv_path)

    assert load.date_column == "일자"
    assert load.value_column == "전체_순매수_거래대금"
    assert load.row_count == 2  # 005930 rows are filtered out for the 000660 target
    assert load.by_date[date(2026, 7, 17)] == Decimal("3000000")
    assert load.by_date[date(2026, 7, 16)] == Decimal("-750000")
    assert list(load.by_date) == sorted(load.by_date)
    assert len(load.file_sha256) == 64


def test_program_csv_loader_accepts_canonical_two_column_schema(tmp_path) -> None:
    csv_path = (tmp_path / "canonical.csv")
    csv_path.write_text(
        "trading_date,program_net_buy_notional\n20260717,3000000\n20260716,-750000\n",
        encoding="utf-8",
    )
    load = load_krx_12009_program_net_buy(csv_path)
    assert load.value_column == "program_net_buy_notional"
    assert load.by_date[date(2026, 7, 17)] == Decimal("3000000")


def test_program_csv_loader_fails_closed_on_ambiguous_and_missing(tmp_path) -> None:
    ambiguous = _write_program_csv(
        tmp_path / "ambiguous.csv",
        ["20260717,000660,SK하이닉스,1,2,3,4"],
        header="일자,종목코드,종목명,차익_순매수_거래대금,비차익_순매수_거래대금,순매수_거래량,순매수_거래대금",
    )
    try:
        load_krx_12009_program_net_buy(ambiguous)
        raise AssertionError("모호한 순매수대금 컬럼은 fail-closed여야 한다")
    except ValueError as exc:
        assert "모호" in str(exc)

    no_value = tmp_path / "novalue.csv"
    no_value.write_text("일자,종가\n20260717,100000\n", encoding="utf-8")
    try:
        load_krx_12009_program_net_buy(no_value)
        raise AssertionError("순매수대금 컬럼 부재는 fail-closed여야 한다")
    except ValueError as exc:
        assert "순매수" in str(exc)


def test_weak_collection_with_program_csv_fills_x_and_marks_available(tmp_path) -> None:
    _write_program_csv(
        tmp_path / "krx12009.csv",
        [
            "20260717,000660,SK하이닉스,1,2,3,3000000",
            "20260716,000660,SK하이닉스,1,2,3,-750000",
        ],
    )
    collection = collect_krx_weak_daily_inputs(
        _WeakCollectionClient(),
        end=date(2026, 7, 17),
        output_path=tmp_path / "weak_inputs.json",
        minimum_trading_days=2,
        max_lookback_calendar_days=5,
        min_request_interval_seconds=0,
        program_csv_path=tmp_path / "krx12009.csv",
    )

    programs = {
        obs.trading_date: obs.program_net_buy_notional
        for obs in collection.observations
    }
    assert programs[date(2026, 7, 17)].value == Decimal("3000000")
    assert programs[date(2026, 7, 16)].value == Decimal("-750000")
    assert programs[date(2026, 7, 17)].missing_reason is None
    assert "MANUAL_CSV" in programs[date(2026, 7, 17)].source

    audit = collection.availability_audit
    assert "krx_program_trading_daily_12009" not in audit.missing_required_datasets
    assert audit.dataset_coverage["krx_program_trading_daily_12009"] == 2
    payload = json.loads((tmp_path / "weak_inputs.json").read_text(encoding="utf-8"))
    assert payload["program_api_catalog_status"] == "MANUAL_CSV_LOADED"
    assert payload["program_manual_csv"]["matched_trading_days"] == 2


def test_weak_collection_program_csv_missing_date_stays_missing_not_zero(tmp_path) -> None:
    _write_program_csv(
        tmp_path / "partial.csv",
        ["20260717,000660,SK하이닉스,1,2,3,3000000"],
    )
    collection = collect_krx_weak_daily_inputs(
        _WeakCollectionClient(),
        end=date(2026, 7, 17),
        output_path=tmp_path / "weak_inputs.json",
        minimum_trading_days=2,
        max_lookback_calendar_days=5,
        min_request_interval_seconds=0,
        program_csv_path=tmp_path / "partial.csv",
    )
    programs = {
        obs.trading_date: obs.program_net_buy_notional
        for obs in collection.observations
    }
    assert programs[date(2026, 7, 17)].value == Decimal("3000000")
    assert programs[date(2026, 7, 16)].value is None
    assert programs[date(2026, 7, 16)].missing_reason == "PROGRAM_12009_CSV_DATE_MISSING"
