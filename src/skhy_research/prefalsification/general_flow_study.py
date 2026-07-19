"""G9 일반수급 사전반증과 D3 음성대조 하네스.

종속변수는 공식 KRX 시가→종가 일수익률인 weak outcome이다.
일반 투자자 수급의 예측력은 레버리지 상품 리밸런싱의 인과 증거가
아니며, 상품 전 기간·유사주·가짜 상장일에서 동일한 모델을 반복해
허위 신호를 찾는다.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import numpy as np

from skhy_research.features.g9_idiosyncratic_flow import G9DailyFeature

GENERAL_FLOW_STUDY_VERSION = "g9_general_flow_prefalsification_v1"
MINIMUM_RAW_TRADING_DAYS = 756
MINIMUM_USABLE_OBSERVATIONS = 735
MINIMUM_REGRESSION_OBSERVATIONS = 30
GENERAL_FLOW_WARNINGS = (
    "일반수급 신호=H1 증명 아님",
    "PROCEED=약한 청신호",
    "FALSIFY≠리밸런싱 기각",
)


class GeneralFlowStatus(StrEnum):
    COMPLETED = "COMPLETED"
    HOLD_DATA_UNAVAILABLE = "HOLD_DATA_UNAVAILABLE"
    HOLD_SAMPLE_INSUFFICIENT = "HOLD_SAMPLE_INSUFFICIENT"


class GeneralFlowVerdict(StrEnum):
    FALSIFY = "FALSIFY"
    PROCEED = "PROCEED"
    HOLD = "HOLD"


class NegativeControlKind(StrEnum):
    PRE_PRODUCT_000660 = "PRE_PRODUCT_000660"
    PEER_SEMICONDUCTOR = "PEER_SEMICONDUCTOR"
    FAKE_LISTING_DATE = "FAKE_LISTING_DATE"


@dataclass(frozen=True)
class FlowReturnObservation:
    trading_date: date
    symbol: str
    open_to_close_return: Decimal
    market_open_utc: int
    official_close_utc: int
    source: str
    input_record_id: str

    def __post_init__(self) -> None:
        if not self.open_to_close_return.is_finite():
            raise ValueError("수익률은 유한해야 한다")
        if self.market_open_utc >= self.official_close_utc:
            raise ValueError("종가시각은 시가시각보다 늦어야 한다")
        if not self.symbol.strip() or not self.source.strip() or not self.input_record_id.strip():
            raise ValueError("수익률에 symbol·source·input_record_id가 필요하다")


@dataclass(frozen=True)
class GeneralFlowRegressionRow:
    trading_date: date
    symbol: str
    x_idio_nb_lag1: Decimal
    short_volume_lag1: Decimal
    short_balance_lag2: Decimal
    y_open_to_close_return: Decimal
    input_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class GeneralFlowRegressionStatistics:
    observation_count: int
    hac_max_lags: int
    intercept: Decimal
    beta: Decimal
    hac_standard_error: Decimal
    t_statistic: Decimal
    analytic_two_sided_p: Decimal
    permutation_p_value: Decimal
    block_bootstrap_ci: tuple[Decimal, Decimal]
    r_squared: Decimal
    x_standard_deviation: Decimal
    predictor_names: tuple[str, ...]


@dataclass(frozen=True)
class GeneralFlowModelAssessment:
    statistics: GeneralFlowRegressionStatistics
    verdict: GeneralFlowVerdict
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class NegativeControlSpec:
    name: str
    kind: NegativeControlKind
    rows: tuple[GeneralFlowRegressionRow, ...]


@dataclass(frozen=True)
class NegativeControlResult:
    name: str
    kind: NegativeControlKind
    observation_count: int
    assessment: GeneralFlowModelAssessment | None
    false_signal_detected: bool


@dataclass(frozen=True)
class GeneralFlowStudyConfig:
    seed: int = 7
    permutations: int = 2000
    bootstrap_resamples: int = 2000
    bootstrap_block_days: int = 20
    minimum_raw_trading_days: int = MINIMUM_RAW_TRADING_DAYS
    minimum_usable_observations: int = MINIMUM_USABLE_OBSERVATIONS

    def __post_init__(self) -> None:
        if self.permutations <= 0 or self.bootstrap_resamples <= 0:
            raise ValueError("permutations와 bootstrap_resamples는 양수여야 한다")
        if self.bootstrap_block_days <= 0:
            raise ValueError("bootstrap_block_days는 양수여야 한다")
        if self.minimum_raw_trading_days < 1 or self.minimum_usable_observations < 1:
            raise ValueError("최소 표본 기준은 양수여야 한다")


@dataclass(frozen=True)
class GeneralFlowStudyResult:
    status: GeneralFlowStatus
    verdict: GeneralFlowVerdict
    reasons: tuple[str, ...]
    scheduled_trading_days: int
    usable_observations: int
    primary_model: GeneralFlowModelAssessment | None
    negative_controls: tuple[NegativeControlResult, ...]
    missing_datasets: tuple[str, ...]
    input_record_ids: tuple[str, ...]
    data_snapshot_hash: str
    warnings: tuple[str, ...] = GENERAL_FLOW_WARNINGS
    study_version: str = GENERAL_FLOW_STUDY_VERSION
    paper_only: bool = True
    order_submission_enabled: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "study_version": self.study_version,
            "status": self.status.value,
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "scheduled_trading_days": self.scheduled_trading_days,
            "usable_observations": self.usable_observations,
            "primary_model": _assessment_dict(self.primary_model),
            "negative_controls": [
                {
                    "name": item.name,
                    "kind": item.kind.value,
                    "observation_count": item.observation_count,
                    "false_signal_detected": item.false_signal_detected,
                    "assessment": _assessment_dict(item.assessment),
                }
                for item in self.negative_controls
            ],
            "missing_datasets": list(self.missing_datasets),
            "input_record_ids": list(self.input_record_ids),
            "data_snapshot_hash": self.data_snapshot_hash,
            "warnings": list(self.warnings),
            "paper_only": self.paper_only,
            "order_submission_enabled": self.order_submission_enabled,
            "outcome_contract": (
                "Y=KRX official log(close/open); X=confirmed investor flow t-1; "
                "short volume=t-1; short balance=t-2"
            ),
        }


def build_general_flow_rows(
    features: Sequence[G9DailyFeature],
    outcomes: Sequence[FlowReturnObservation],
    *,
    symbol: str,
) -> tuple[GeneralFlowRegressionRow, ...]:
    by_date = {
        item.trading_date: item for item in outcomes if item.symbol == symbol
    }
    if len(by_date) != sum(item.symbol == symbol for item in outcomes):
        raise ValueError(f"DUPLICATE_OUTCOME:{symbol}")
    rows: list[GeneralFlowRegressionRow] = []
    for feature in features:
        outcome = by_date.get(feature.trading_date)
        if outcome is None:
            continue
        if feature.available_at_utc > outcome.market_open_utc:
            continue
        if feature.short_volume_lag1 is None or feature.short_balance_lag2 is None:
            continue
        rows.append(
            GeneralFlowRegressionRow(
                trading_date=feature.trading_date,
                symbol=symbol,
                x_idio_nb_lag1=feature.idio_nb_000660_lag1,
                short_volume_lag1=feature.short_volume_lag1,
                short_balance_lag2=feature.short_balance_lag2,
                y_open_to_close_return=outcome.open_to_close_return,
                input_record_ids=tuple(
                    dict.fromkeys((*feature.input_record_ids, outcome.input_record_id))
                ),
            )
        )
    return tuple(rows)


def fit_general_flow_regression(
    rows: Sequence[GeneralFlowRegressionRow],
    config: GeneralFlowStudyConfig | None = None,
    *,
    seed_offset: int = 0,
) -> GeneralFlowRegressionStatistics:
    resolved = config or GeneralFlowStudyConfig()
    if len(rows) < MINIMUM_REGRESSION_OBSERVATIONS:
        raise ValueError("GENERAL_FLOW_REGRESSION_SAMPLE_INSUFFICIENT")
    ordered = tuple(sorted(rows, key=lambda item: item.trading_date))
    raw_predictors = np.asarray(
        [
            [
                float(item.x_idio_nb_lag1),
                float(item.short_volume_lag1),
                float(item.short_balance_lag2),
            ]
            for item in ordered
        ],
        dtype=float,
    )
    y = np.asarray([float(item.y_open_to_close_return) for item in ordered], dtype=float)
    means = np.mean(raw_predictors, axis=0)
    scales = np.std(raw_predictors, axis=0, ddof=0)
    if np.any(~np.isfinite(scales)) or np.any(scales <= 0):
        raise ValueError("GENERAL_FLOW_PREDICTOR_ZERO_VARIANCE")
    predictors = (raw_predictors - means) / scales
    design = np.column_stack((np.ones(len(ordered)), predictors))
    if int(np.linalg.matrix_rank(design)) < design.shape[1]:
        raise ValueError("GENERAL_FLOW_DESIGN_RANK_DEFICIENT")
    coefficients, residuals = _ols(design, y)
    hac_lags = max(1, int(math.floor(4 * (len(ordered) / 100) ** (2 / 9))))
    covariance = _newey_west_covariance(design, residuals, hac_lags)
    hac_se = math.sqrt(max(0.0, float(covariance[1, 1])))
    if hac_se == 0 or not math.isfinite(hac_se):
        raise ValueError("GENERAL_FLOW_HAC_NOT_IDENTIFIED")
    beta = float(coefficients[1])
    t_stat = beta / hac_se
    analytic_p = math.erfc(abs(t_stat) / math.sqrt(2))
    permutation_p = _permutation_p_value(
        design,
        y,
        observed_beta=beta,
        count=resolved.permutations,
        seed=resolved.seed + seed_offset,
    )
    bootstrap_ci = _block_bootstrap_ci(
        design,
        y,
        count=resolved.bootstrap_resamples,
        block_days=resolved.bootstrap_block_days,
        seed=resolved.seed + 100_000 + seed_offset,
    )
    total = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 0.0 if total == 0 else 1 - float(residuals @ residuals) / total
    return GeneralFlowRegressionStatistics(
        observation_count=len(ordered),
        hac_max_lags=hac_lags,
        intercept=Decimal(str(coefficients[0])),
        beta=Decimal(str(beta)),
        hac_standard_error=Decimal(str(hac_se)),
        t_statistic=Decimal(str(t_stat)),
        analytic_two_sided_p=Decimal(str(analytic_p)),
        permutation_p_value=Decimal(str(permutation_p)),
        block_bootstrap_ci=(Decimal(str(bootstrap_ci[0])), Decimal(str(bootstrap_ci[1]))),
        r_squared=Decimal(str(r_squared)),
        x_standard_deviation=Decimal(str(scales[0])),
        predictor_names=(
            "idio_nb_000660_lag1",
            "short_volume_lag1",
            "short_balance_lag2",
        ),
    )


def assess_general_flow_statistics(
    statistics: GeneralFlowRegressionStatistics,
) -> GeneralFlowModelAssessment:
    reasons: list[str] = []
    lower, upper = statistics.block_bootstrap_ci
    if statistics.beta <= 0:
        reasons.append("EXPECTED_POSITIVE_SIGN_NOT_MET")
    if abs(statistics.t_statistic) <= Decimal("1.96"):
        reasons.append("ABS_HAC_T_NOT_ABOVE_1_96")
    if lower <= 0 <= upper:
        reasons.append("BLOCK_BOOTSTRAP_CI_INCLUDES_ZERO")
    if statistics.permutation_p_value >= Decimal("0.05"):
        reasons.append("DATE_PERMUTATION_P_NOT_BELOW_0_05")
    return GeneralFlowModelAssessment(
        statistics=statistics,
        verdict=GeneralFlowVerdict.FALSIFY if reasons else GeneralFlowVerdict.PROCEED,
        reasons=tuple(reasons),
    )


def run_negative_control_harness(
    specs: Sequence[NegativeControlSpec],
    config: GeneralFlowStudyConfig | None = None,
) -> tuple[NegativeControlResult, ...]:
    resolved = config or GeneralFlowStudyConfig()
    results: list[NegativeControlResult] = []
    for index, spec in enumerate(specs, start=1):
        try:
            statistics = fit_general_flow_regression(
                spec.rows, resolved, seed_offset=index * 10_000
            )
        except ValueError:
            assessment = None
        else:
            assessment = assess_general_flow_statistics(statistics)
        results.append(
            NegativeControlResult(
                name=spec.name,
                kind=spec.kind,
                observation_count=len(spec.rows),
                assessment=assessment,
                false_signal_detected=(
                    assessment is not None
                    and assessment.verdict is GeneralFlowVerdict.PROCEED
                ),
            )
        )
    return tuple(results)


def build_negative_control_specs(
    primary_rows: Sequence[GeneralFlowRegressionRow],
    *,
    peer_rows: Mapping[str, Sequence[GeneralFlowRegressionRow]],
    actual_product_listing_date: date,
    fake_listing_dates: Sequence[date],
    fake_window_trading_days: int = 120,
) -> tuple[NegativeControlSpec, ...]:
    if fake_window_trading_days < MINIMUM_REGRESSION_OBSERVATIONS:
        raise ValueError("fake listing window가 회귀 최소 표본보다 짧다")
    ordered_primary = tuple(sorted(primary_rows, key=lambda item: item.trading_date))
    specs: list[NegativeControlSpec] = [
        NegativeControlSpec(
            name="000660_before_actual_product",
            kind=NegativeControlKind.PRE_PRODUCT_000660,
            rows=tuple(
                item
                for item in ordered_primary
                if item.trading_date < actual_product_listing_date
            ),
        )
    ]
    specs.extend(
        NegativeControlSpec(
            name=f"peer_{symbol}",
            kind=NegativeControlKind.PEER_SEMICONDUCTOR,
            rows=tuple(sorted(rows, key=lambda item: item.trading_date)),
        )
        for symbol, rows in sorted(peer_rows.items())
    )
    for fake_date in sorted(fake_listing_dates):
        candidates = [item for item in ordered_primary if item.trading_date >= fake_date]
        specs.append(
            NegativeControlSpec(
                name=f"fake_listing_{fake_date.isoformat()}",
                kind=NegativeControlKind.FAKE_LISTING_DATE,
                rows=tuple(candidates[:fake_window_trading_days]),
            )
        )
    return tuple(specs)


def run_general_flow_study(
    primary_rows: Sequence[GeneralFlowRegressionRow],
    *,
    scheduled_trading_days: int,
    negative_control_specs: Sequence[NegativeControlSpec],
    config: GeneralFlowStudyConfig | None = None,
    missing_datasets: Sequence[str] = (),
) -> GeneralFlowStudyResult:
    resolved = config or GeneralFlowStudyConfig()
    ordered = tuple(sorted(primary_rows, key=lambda item: item.trading_date))
    lineage = tuple(
        dict.fromkeys(record_id for row in ordered for record_id in row.input_record_ids)
    )
    snapshot_hash = _snapshot_hash(ordered, missing_datasets)
    if missing_datasets:
        return GeneralFlowStudyResult(
            status=GeneralFlowStatus.HOLD_DATA_UNAVAILABLE,
            verdict=GeneralFlowVerdict.HOLD,
            reasons=tuple(f"MISSING_DATASET:{item}" for item in missing_datasets),
            scheduled_trading_days=scheduled_trading_days,
            usable_observations=len(ordered),
            primary_model=None,
            negative_controls=(),
            missing_datasets=tuple(missing_datasets),
            input_record_ids=lineage,
            data_snapshot_hash=snapshot_hash,
        )
    try:
        primary = assess_general_flow_statistics(
            fit_general_flow_regression(ordered, resolved)
        )
    except ValueError:
        primary = None
    if primary is None:
        return GeneralFlowStudyResult(
            status=GeneralFlowStatus.HOLD_DATA_UNAVAILABLE,
            verdict=GeneralFlowVerdict.HOLD,
            reasons=("GENERAL_FLOW_MODEL_NOT_COMPUTABLE",),
            scheduled_trading_days=scheduled_trading_days,
            usable_observations=len(ordered),
            primary_model=None,
            negative_controls=(),
            missing_datasets=(),
            input_record_ids=lineage,
            data_snapshot_hash=snapshot_hash,
        )
    controls = run_negative_control_harness(negative_control_specs, resolved)
    if (
        scheduled_trading_days < resolved.minimum_raw_trading_days
        or len(ordered) < resolved.minimum_usable_observations
    ):
        return GeneralFlowStudyResult(
            status=GeneralFlowStatus.HOLD_SAMPLE_INSUFFICIENT,
            verdict=GeneralFlowVerdict.HOLD,
            reasons=("PRD_10_2_MINIMUM_756_TRADING_DAYS_NOT_MET",),
            scheduled_trading_days=scheduled_trading_days,
            usable_observations=len(ordered),
            primary_model=primary,
            negative_controls=controls,
            missing_datasets=(),
            input_record_ids=lineage,
            data_snapshot_hash=snapshot_hash,
        )
    if primary.verdict is GeneralFlowVerdict.FALSIFY:
        return _completed_result(
            primary,
            controls,
            ordered,
            scheduled_trading_days,
            lineage,
            snapshot_hash,
            reasons=primary.reasons,
            verdict=GeneralFlowVerdict.FALSIFY,
        )
    required_kinds = {
        NegativeControlKind.PRE_PRODUCT_000660,
        NegativeControlKind.PEER_SEMICONDUCTOR,
        NegativeControlKind.FAKE_LISTING_DATE,
    }
    completed_kinds = {
        item.kind for item in controls if item.assessment is not None
    }
    if not required_kinds.issubset(completed_kinds):
        return GeneralFlowStudyResult(
            status=GeneralFlowStatus.HOLD_DATA_UNAVAILABLE,
            verdict=GeneralFlowVerdict.HOLD,
            reasons=("D3_NEGATIVE_CONTROL_INCOMPLETE",),
            scheduled_trading_days=scheduled_trading_days,
            usable_observations=len(ordered),
            primary_model=primary,
            negative_controls=controls,
            missing_datasets=(),
            input_record_ids=lineage,
            data_snapshot_hash=snapshot_hash,
        )
    false_signals = tuple(item.name for item in controls if item.false_signal_detected)
    if false_signals:
        return _completed_result(
            primary,
            controls,
            ordered,
            scheduled_trading_days,
            lineage,
            snapshot_hash,
            reasons=tuple(f"D3_FALSE_SIGNAL:{name}" for name in false_signals),
            verdict=GeneralFlowVerdict.FALSIFY,
        )
    return _completed_result(
        primary,
        controls,
        ordered,
        scheduled_trading_days,
        lineage,
        snapshot_hash,
        reasons=("GENERAL_FLOW_WEAK_GREEN_LIGHT_ONLY",),
        verdict=GeneralFlowVerdict.PROCEED,
    )


def build_general_flow_hold(
    *,
    missing_datasets: Sequence[str],
    scheduled_trading_days: int = 0,
    input_record_ids: Sequence[str] = (),
) -> GeneralFlowStudyResult:
    ordered_missing = tuple(dict.fromkeys(missing_datasets))
    digest = _snapshot_hash((), ordered_missing)
    return GeneralFlowStudyResult(
        status=GeneralFlowStatus.HOLD_DATA_UNAVAILABLE,
        verdict=GeneralFlowVerdict.HOLD,
        reasons=tuple(f"MISSING_DATASET:{item}" for item in ordered_missing),
        scheduled_trading_days=scheduled_trading_days,
        usable_observations=0,
        primary_model=None,
        negative_controls=(),
        missing_datasets=ordered_missing,
        input_record_ids=tuple(input_record_ids),
        data_snapshot_hash=digest,
    )


def write_general_flow_reports(
    result: GeneralFlowStudyResult,
    output_directory: Path,
    *,
    basename: str = "g9_general_flow_prefalsification",
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / f"{basename}.json"
    markdown_path = output_directory / f"{basename}.md"
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown_report(result), encoding="utf-8")
    return json_path, markdown_path


def _ols(design: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
    return coefficients, y - design @ coefficients


def _newey_west_covariance(
    design: np.ndarray, residuals: np.ndarray, max_lags: int
) -> np.ndarray:
    bread = np.linalg.inv(design.T @ design)
    xu = design * residuals[:, None]
    meat = xu.T @ xu
    for lag in range(1, min(max_lags, len(design) - 1) + 1):
        weight = 1 - lag / (max_lags + 1)
        gamma = xu[lag:].T @ xu[:-lag]
        meat += weight * (gamma + gamma.T)
    return bread @ meat @ bread


def _permutation_p_value(
    design: np.ndarray,
    y: np.ndarray,
    *,
    observed_beta: float,
    count: int,
    seed: int,
) -> float:
    rng = np.random.default_rng(seed)
    exceedances = 0
    for _ in range(count):
        permuted = design.copy()
        permuted[:, 1] = rng.permutation(permuted[:, 1])
        beta = float(np.linalg.lstsq(permuted, y, rcond=None)[0][1])
        exceedances += abs(beta) >= abs(observed_beta)
    return (exceedances + 1) / (count + 1)


def _block_bootstrap_ci(
    design: np.ndarray,
    y: np.ndarray,
    *,
    count: int,
    block_days: int,
    seed: int,
) -> tuple[float, float]:
    block_size = min(block_days, len(design))
    starts = np.arange(0, len(design) - block_size + 1)
    blocks_needed = math.ceil(len(design) / block_size)
    rng = np.random.default_rng(seed)
    betas: list[float] = []
    for _ in range(count):
        selected = rng.choice(starts, size=blocks_needed, replace=True)
        indices = np.concatenate(
            [np.arange(start, start + block_size) for start in selected]
        )[: len(design)]
        sample_design = design[indices]
        if int(np.linalg.matrix_rank(sample_design)) < sample_design.shape[1]:
            continue
        betas.append(float(np.linalg.lstsq(sample_design, y[indices], rcond=None)[0][1]))
    if len(betas) < max(20, int(count * 0.9)):
        raise ValueError("GENERAL_FLOW_BLOCK_BOOTSTRAP_NOT_IDENTIFIED")
    return float(np.quantile(betas, 0.025)), float(np.quantile(betas, 0.975))


def _completed_result(
    primary: GeneralFlowModelAssessment,
    controls: tuple[NegativeControlResult, ...],
    rows: Sequence[GeneralFlowRegressionRow],
    scheduled_trading_days: int,
    lineage: tuple[str, ...],
    snapshot_hash: str,
    *,
    reasons: tuple[str, ...],
    verdict: GeneralFlowVerdict,
) -> GeneralFlowStudyResult:
    return GeneralFlowStudyResult(
        status=GeneralFlowStatus.COMPLETED,
        verdict=verdict,
        reasons=reasons,
        scheduled_trading_days=scheduled_trading_days,
        usable_observations=len(rows),
        primary_model=primary,
        negative_controls=controls,
        missing_datasets=(),
        input_record_ids=lineage,
        data_snapshot_hash=snapshot_hash,
    )


def _snapshot_hash(
    rows: Sequence[GeneralFlowRegressionRow], missing_datasets: Sequence[str]
) -> str:
    import hashlib

    payload = {
        "rows": [
            {
                "date": item.trading_date.isoformat(),
                "symbol": item.symbol,
                "records": item.input_record_ids,
            }
            for item in rows
        ],
        "missing": list(missing_datasets),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _assessment_dict(
    assessment: GeneralFlowModelAssessment | None,
) -> dict[str, object] | None:
    if assessment is None:
        return None
    stats = assessment.statistics
    return {
        "verdict": assessment.verdict.value,
        "reasons": list(assessment.reasons),
        "observation_count": stats.observation_count,
        "hac_max_lags": stats.hac_max_lags,
        "intercept": str(stats.intercept),
        "beta_per_x_std": str(stats.beta),
        "hac_standard_error": str(stats.hac_standard_error),
        "t_statistic": str(stats.t_statistic),
        "analytic_two_sided_p": str(stats.analytic_two_sided_p),
        "permutation_p_value": str(stats.permutation_p_value),
        "block_bootstrap_ci": [
            str(stats.block_bootstrap_ci[0]),
            str(stats.block_bootstrap_ci[1]),
        ],
        "r_squared": str(stats.r_squared),
        "x_standard_deviation": str(stats.x_standard_deviation),
        "predictor_names": list(stats.predictor_names),
    }


def _markdown_report(result: GeneralFlowStudyResult) -> str:
    lines = [
        "# G9 일반수급 사전반증",
        "",
        f"- 상태: `{result.status.value}`",
        f"- 판정: `{result.verdict.value}`",
        f"- 표본: scheduled={result.scheduled_trading_days}, usable={result.usable_observations}",
        "- 모델: OLS + HAC, date permutation, moving-block bootstrap",
        "- X 시점: 투자자 순매수 t-1, 공매도 거래량 t-1, 잔고 t-2",
        "- Y: KRX 공식 시가→종가 로그수익률(weak outcome)",
        "- 주문 제출: 비활성화",
        "",
        "## 판정 사유",
        "",
        *(f"- `{reason}`" for reason in result.reasons),
        "",
        "## 주 회귀",
        "",
        *_assessment_markdown(result.primary_model),
        "## D3 음성대조",
        "",
    ]
    for control in result.negative_controls:
        lines.extend(
            [
                f"### {control.name}",
                "",
                f"- kind={control.kind.value}, n={control.observation_count}",
                f"- false_signal_detected={control.false_signal_detected}",
                *_assessment_markdown(control.assessment),
            ]
        )
    if result.missing_datasets:
        lines.extend(
            ["## 필요 데이터", "", *(f"- {item}" for item in result.missing_datasets), ""]
        )
    lines.extend(["## 경고", "", *(f"- {item}" for item in result.warnings), ""])
    return "\n".join(lines)


def _assessment_markdown(
    assessment: GeneralFlowModelAssessment | None,
) -> list[str]:
    if assessment is None:
        return ["`NOT_COMPUTABLE`", ""]
    stats = assessment.statistics
    return [
        f"- verdict={assessment.verdict.value}, beta/std(X)={stats.beta}",
        f"- HAC t={stats.t_statistic}, analytic p={stats.analytic_two_sided_p}",
        f"- permutation p={stats.permutation_p_value}",
        f"- block bootstrap 95% CI={stats.block_bootstrap_ci}",
        f"- reasons={list(assessment.reasons)}",
        "",
    ]
