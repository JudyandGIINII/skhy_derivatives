"""Round 3 연속장 OFI/program proxy 회귀와 검증 하네스.

페이퍼 연구 전용이다. 이 모듈은 broker·주문 API를 조립하지 않는다. sanitized fixture는
계산·계약 테스트에만 쓸 수 있고 source/PASS gate에서는 항상 HOLD된다.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from skhy_research.domain.enums import PromotionVerdict, SignalDirection
from skhy_research.features.h1_close_pressure.continuous_ofi import (
    H1_CONTINUOUS_OFI_MODEL_VERSION,
    FeatureDataOrigin,
)
from skhy_research.features.h1_close_pressure.theoretical_exposure import (
    theoretical_delta_exposure,
)

FLOW_FEATURE_NAMES = ("x_ofi", "x_depth", "x_micro", "x_program", "x_conflict")
HUBER_DELTA_MULTIPLIERS = (1.20, 1.35, 1.50)
ELASTIC_NET_LAMBDAS = (1e-4, 1e-3, 1e-2, 1e-1, 1.0)
ELASTIC_NET_RHOS = (0.0, 0.25, 0.50, 0.75, 1.0)
REGIME = "single"
INITIAL_TRAIN_DAYS = 60
VALIDATION_DAYS = 30
SEALED_TEST_DAYS = 30
INNER_MIN_TRAIN_DAYS = 20
INNER_FOLD_DAYS = 10
RESEARCH_CAPITAL_KRW = Decimal("20000000")
TARGET_NOTIONAL_KRW = Decimal("1000000")
SIGNAL_COST_MULTIPLIER = Decimal("2.0")
ENTRY_FILL_VERSION = "h1_continuous_fok_entry@1.0.0"
EXIT_FILL_VERSION = "h1_close_auction_outcome@1.0.0"
CALIBRATION_USAGE = "POST_HOC_DIAGNOSTIC_ONLY"


class RegressionStatus(StrEnum):
    FITTED = "FITTED"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"


class TargetStatus(StrEnum):
    COMPUTABLE = "COMPUTABLE"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"


class CalibrationStatus(StrEnum):
    IDENTIFIED = "IDENTIFIED"
    CALIBRATION_NOT_IDENTIFIED = "CALIBRATION_NOT_IDENTIFIED"


class ValidationStage(StrEnum):
    SOURCE_GATE = "SOURCE_GATE"
    TRAIN_ONLY_SELECTION = "TRAIN_ONLY_SELECTION"
    CHEAP_REJECT = "CHEAP_REJECT"
    SEALED_TEST = "SEALED_TEST"
    WALK_FORWARD = "WALK_FORWARD"
    FINAL_PASS_GATE = "FINAL_PASS_GATE"


@dataclass(frozen=True)
class PaperFill:
    price: Decimal | None
    quantity: int
    target_quantity: int
    event_time_utc: int | None
    missing_reason: str | None = None
    input_age_seconds: Decimal | None = Decimal("0")
    complete_ten_levels: bool = True
    uncrossed_book: bool = True

    @property
    def is_full_fill(self) -> bool:
        return (
            self.price is not None
            and self.price.is_finite()
            and self.price > 0
            and self.quantity == self.target_quantity
            and self.target_quantity > 0
            and self.event_time_utc is not None
            and self.input_age_seconds is not None
            and Decimal("0") <= self.input_age_seconds <= Decimal("2")
            and self.complete_ten_levels
            and self.uncrossed_book
        )


@dataclass(frozen=True)
class CloseReturnTarget:
    value: Decimal | None
    status: TargetStatus
    p_entry_ref: Decimal | None
    missing_reasons: tuple[str, ...]
    entry_fill_version: str = ENTRY_FILL_VERSION
    exit_fill_version: str = EXIT_FILL_VERSION


def build_close_return_target(
    buy_fill: PaperFill,
    sell_fill: PaperFill,
    *,
    official_close_price: Decimal | None,
    official_close_available_at_utc: int | None,
    entry_deadline_utc: int,
    outcome_deadline_utc: int,
    decision_time_utc: int | None = None,
) -> CloseReturnTarget:
    """양방향 FOK 실행가능가 중심 대비 공식 종가수익을 만든다."""

    reasons: list[str] = []
    for side, fill in (("BUY", buy_fill), ("SELL", sell_fill)):
        if not fill.is_full_fill:
            reasons.append(fill.missing_reason or f"{side}_FILL_NOT_AVAILABLE")
        elif fill.event_time_utc is None or fill.event_time_utc > entry_deadline_utc:
            reasons.append("ENTRY_TIMEOUT")
        elif decision_time_utc is not None and fill.event_time_utc < decision_time_utc:
            reasons.append("PRE_DECISION_FILL_FORBIDDEN")
    if buy_fill.target_quantity != sell_fill.target_quantity:
        reasons.append("TARGET_QUANTITY_MISMATCH")
    if official_close_price is None or not official_close_price.is_finite():
        reasons.append("EXIT_OUTCOME_MISSING")
    elif official_close_price <= 0:
        reasons.append("OFFICIAL_CLOSE_INVALID")
    if (
        official_close_available_at_utc is None
        or official_close_available_at_utc > outcome_deadline_utc
    ):
        reasons.append("EXIT_OUTCOME_MISSING")
    if reasons:
        return CloseReturnTarget(
            value=None,
            status=TargetStatus.NOT_COMPUTABLE,
            p_entry_ref=None,
            missing_reasons=tuple(dict.fromkeys(reasons)),
        )
    assert buy_fill.price is not None
    assert sell_fill.price is not None
    assert official_close_price is not None
    entry_reference = (buy_fill.price + sell_fill.price) / Decimal("2")
    if entry_reference <= 0:
        return CloseReturnTarget(
            value=None,
            status=TargetStatus.NOT_COMPUTABLE,
            p_entry_ref=None,
            missing_reasons=("ENTRY_REFERENCE_INVALID",),
        )
    return CloseReturnTarget(
        value=official_close_price / entry_reference - Decimal("1"),
        status=TargetStatus.COMPUTABLE,
        p_entry_ref=entry_reference,
        missing_reasons=(),
    )


def calculate_theoretical_z(
    *,
    beta: Decimal,
    prior_nav: Decimal,
    underlying_return: Decimal,
    underlying_20d_adv_notional: Decimal,
) -> Decimal:
    if underlying_20d_adv_notional <= 0:
        raise ValueError("underlying_20d_adv_notional은 0보다 커야 한다")
    return theoretical_delta_exposure(beta, prior_nav, underlying_return) / (
        underlying_20d_adv_notional
    )


@dataclass(frozen=True)
class RegressionSample:
    trading_date: date
    theoretical_z: Mapping[str, Decimal | float | None]
    flow_features: Mapping[str, Decimal | float | None]
    target_return: Decimal | float | None
    target_missing_reason: str | None = None
    data_origin: FeatureDataOrigin = FeatureDataOrigin.SANITIZED_FIXTURE

    def __post_init__(self) -> None:
        if self.target_return is None and not self.target_missing_reason:
            raise ValueError("target 결측 sample에는 target_missing_reason이 필요하다")
        if self.target_return is not None and self.target_missing_reason is not None:
            raise ValueError("관측 target에는 target_missing_reason을 둘 수 없다")


@dataclass(frozen=True, order=True)
class HuberElasticNetHyperparameters:
    delta_multiplier: float
    lambda_: float
    rho: float

    def __post_init__(self) -> None:
        if self.delta_multiplier not in HUBER_DELTA_MULTIPLIERS:
            raise ValueError("delta_multiplier가 봉인 grid 밖이다")
        if self.lambda_ not in ELASTIC_NET_LAMBDAS:
            raise ValueError("lambda가 봉인 grid 밖이다")
        if self.rho not in ELASTIC_NET_RHOS:
            raise ValueError("rho가 봉인 grid 밖이다")


@dataclass(frozen=True)
class RobustScalerState:
    medians: Mapping[str, float]
    iqrs: Mapping[str, float]
    active_feature_names: tuple[str, ...]
    zero_variance_feature_names: tuple[str, ...]

    def transform(self, values: Mapping[str, Decimal | float | None]) -> np.ndarray | None:
        transformed: list[float] = []
        for name in self.active_feature_names:
            value = _finite_float(values.get(name))
            if value is None:
                return None
            transformed.append((value - self.medians[name]) / self.iqrs[name])
        return np.asarray(transformed, dtype=float)


@dataclass(frozen=True)
class CandidateScore:
    hyperparameters: HuberElasticNetHyperparameters
    mean_huber_loss: float
    standard_error: float
    mean_nonzero_coefficients: float
    fold_losses: tuple[float, ...]


@dataclass(frozen=True)
class RegressionPrediction:
    gross_return: Decimal
    pressure_without_intercept: Decimal
    theoretical_component: Decimal
    flow_component: Decimal
    observable_flow_adjustment_proxy: Decimal | None


@dataclass(frozen=True)
class SealedRegressionModel:
    include_flow_features: bool
    hyperparameters: HuberElasticNetHyperparameters
    intercept: float
    coefficients: Mapping[str, float]
    scaler: RobustScalerState
    sigma_y: float
    train_dates: tuple[date, ...]
    flags: tuple[str, ...]
    model_hash: str
    regime: str = REGIME
    model_version: str = H1_CONTINUOUS_OFI_MODEL_VERSION
    selected_from_train_only: bool = True

    def predict(
        self,
        sample: RegressionSample,
        *,
        underlying_20d_adv_notional: Decimal | None = None,
    ) -> RegressionPrediction | None:
        values = _sample_values(sample, include_flow=self.include_flow_features)
        scaled = self.scaler.transform(values)
        if scaled is None:
            return None
        names = self.scaler.active_feature_names
        coefficients = np.asarray([self.coefficients[name] for name in names], dtype=float)
        contributions = scaled * coefficients
        theory = sum(
            contribution
            for name, contribution in zip(names, contributions, strict=True)
            if name.startswith("z:")
        )
        flow = sum(
            contribution
            for name, contribution in zip(names, contributions, strict=True)
            if name in FLOW_FEATURE_NAMES
        )
        pressure = theory + flow
        observable_adjustment: Decimal | None = None
        if underlying_20d_adv_notional is not None:
            if underlying_20d_adv_notional <= 0:
                raise ValueError("underlying_20d_adv_notional은 0보다 커야 한다")
            observable_adjustment = underlying_20d_adv_notional * Decimal(str(flow))
        return RegressionPrediction(
            gross_return=Decimal(str(self.intercept + pressure)),
            pressure_without_intercept=Decimal(str(pressure)),
            theoretical_component=Decimal(str(theory)),
            flow_component=Decimal(str(flow)),
            observable_flow_adjustment_proxy=observable_adjustment,
        )


@dataclass(frozen=True)
class RegressionFitResult:
    status: RegressionStatus
    model: SealedRegressionModel | None
    reason: str | None
    candidate_scores: tuple[CandidateScore, ...]
    scheduled_sample_count: int
    usable_sample_count: int
    missing_reason_counts: Mapping[str, int]


def fit_sealed_huber_elastic_net(
    train_samples: Sequence[RegressionSample],
    *,
    include_flow_features: bool = True,
) -> RegressionFitResult:
    """train 내부 expanding fold만으로 grid를 선택하고 full train에 재적합한다."""

    samples = tuple(sorted(train_samples, key=lambda sample: sample.trading_date))
    feature_names = _feature_names(samples, include_flow_features)
    usable, missing_reasons = _usable_samples(samples, feature_names, include_flow_features)
    if len(usable) < INNER_MIN_TRAIN_DAYS + INNER_FOLD_DAYS:
        return _fit_failure(
            "INSUFFICIENT_TRAIN_TARGETS",
            len(samples),
            len(usable),
            missing_reasons,
        )
    y_all = np.asarray([_required_target(sample) for sample in usable], dtype=float)
    if _robust_target_scale(y_all) == 0:
        return _fit_failure("TARGET_SCALE_ZERO", len(samples), len(usable), missing_reasons)

    scores: list[CandidateScore] = []
    for delta_multiplier in HUBER_DELTA_MULTIPLIERS:
        for lambda_ in ELASTIC_NET_LAMBDAS:
            for rho in ELASTIC_NET_RHOS:
                hyperparameters = HuberElasticNetHyperparameters(
                    delta_multiplier=delta_multiplier,
                    lambda_=lambda_,
                    rho=rho,
                )
                score = _score_candidate(usable, feature_names, hyperparameters)
                if score is not None:
                    scores.append(score)
    if not scores:
        return _fit_failure("INNER_FOLD_NOT_COMPUTABLE", len(samples), len(usable), missing_reasons)
    selected = _one_standard_error_selection(scores)
    model = _fit_fixed_model(usable, feature_names, selected.hyperparameters)
    if model is None:
        return _fit_failure("TARGET_SCALE_ZERO", len(samples), len(usable), missing_reasons)
    return RegressionFitResult(
        status=RegressionStatus.FITTED,
        model=model,
        reason=None,
        candidate_scores=tuple(scores),
        scheduled_sample_count=len(samples),
        usable_sample_count=len(usable),
        missing_reason_counts=dict(sorted(missing_reasons.items())),
    )


def fit_fixed_huber_elastic_net(
    train_samples: Sequence[RegressionSample],
    hyperparameters: HuberElasticNetHyperparameters,
    *,
    include_flow_features: bool = True,
) -> RegressionFitResult:
    """단위·재현 테스트용 고정 후보 적합. 후보 선택을 수행하지 않는다."""

    samples = tuple(sorted(train_samples, key=lambda sample: sample.trading_date))
    feature_names = _feature_names(samples, include_flow_features)
    usable, missing_reasons = _usable_samples(samples, feature_names, include_flow_features)
    if not usable:
        return _fit_failure("INSUFFICIENT_TRAIN_TARGETS", len(samples), 0, missing_reasons)
    model = _fit_fixed_model(usable, feature_names, hyperparameters)
    if model is None:
        return _fit_failure("TARGET_SCALE_ZERO", len(samples), len(usable), missing_reasons)
    return RegressionFitResult(
        status=RegressionStatus.FITTED,
        model=model,
        reason=None,
        candidate_scores=(),
        scheduled_sample_count=len(samples),
        usable_sample_count=len(usable),
        missing_reason_counts=dict(sorted(missing_reasons.items())),
    )


def _score_candidate(
    samples: tuple[RegressionSample, ...],
    feature_names: tuple[str, ...],
    hyperparameters: HuberElasticNetHyperparameters,
) -> CandidateScore | None:
    losses: list[float] = []
    nonzero_counts: list[int] = []
    train_end = INNER_MIN_TRAIN_DAYS
    while train_end + INNER_FOLD_DAYS <= len(samples):
        fold_train = samples[:train_end]
        fold_validation = samples[train_end : train_end + INNER_FOLD_DAYS]
        model = _fit_fixed_model(fold_train, feature_names, hyperparameters)
        if model is None:
            return None
        validation_y: list[float] = []
        predictions: list[float] = []
        for sample in fold_validation:
            prediction = model.predict(sample)
            if prediction is None:
                continue
            validation_y.append(_required_target(sample))
            predictions.append(float(prediction.gross_return))
        if len(predictions) != len(fold_validation):
            return None
        residuals = np.asarray(validation_y) - np.asarray(predictions)
        delta = hyperparameters.delta_multiplier * model.sigma_y
        losses.append(float(np.mean(_huber_values(residuals, delta))))
        nonzero_counts.append(sum(abs(value) > 1e-12 for value in model.coefficients.values()))
        train_end += INNER_FOLD_DAYS
    if not losses:
        return None
    standard_error = (
        float(np.std(losses, ddof=1) / math.sqrt(len(losses))) if len(losses) > 1 else 0.0
    )
    return CandidateScore(
        hyperparameters=hyperparameters,
        mean_huber_loss=float(np.mean(losses)),
        standard_error=standard_error,
        mean_nonzero_coefficients=float(np.mean(nonzero_counts)),
        fold_losses=tuple(losses),
    )


def _one_standard_error_selection(scores: Sequence[CandidateScore]) -> CandidateScore:
    best = min(scores, key=lambda score: score.mean_huber_loss)
    ceiling = best.mean_huber_loss + best.standard_error
    eligible = [score for score in scores if score.mean_huber_loss <= ceiling + 1e-15]
    return min(
        eligible,
        key=lambda score: (
            -score.hyperparameters.lambda_,
            score.mean_nonzero_coefficients,
            -score.hyperparameters.rho,
            score.hyperparameters.delta_multiplier,
            score.mean_huber_loss,
        ),
    )


def _fit_fixed_model(
    samples: Sequence[RegressionSample],
    feature_names: tuple[str, ...],
    hyperparameters: HuberElasticNetHyperparameters,
) -> SealedRegressionModel | None:
    raw_values = [
        _sample_values(sample, include_flow=bool(set(FLOW_FEATURE_NAMES) & set(feature_names)))
        for sample in samples
    ]
    matrix = np.asarray(
        [[_required_float(values[name]) for name in feature_names] for values in raw_values],
        dtype=float,
    )
    targets = np.asarray([_required_target(sample) for sample in samples], dtype=float)
    sigma_y = _robust_target_scale(targets)
    if sigma_y == 0:
        return None
    scaler, scaled_matrix = _fit_robust_scaler(matrix, feature_names)
    delta = hyperparameters.delta_multiplier * sigma_y
    intercept, active_coefficients = _proximal_huber_elastic_net(
        scaled_matrix,
        targets,
        delta=delta,
        lambda_=hyperparameters.lambda_,
        rho=hyperparameters.rho,
    )
    coefficients = {
        name: float(value)
        for name, value in zip(scaler.active_feature_names, active_coefficients, strict=True)
    }
    flags = [f"ZERO_VARIANCE_FEATURE:{name}" for name in scaler.zero_variance_feature_names]
    theory_names = tuple(name for name in feature_names if name.startswith("z:"))
    for name in theory_names:
        if name in coefficients and abs(coefficients[name]) <= 1e-12:
            coefficients[name] = 0.0
            flags.append(f"THEORY_TERM_ELIMINATED:{name[2:]}")
    active_theory_names = tuple(name for name in theory_names if name in coefficients)
    if (
        active_theory_names
        and len(active_theory_names) == len(theory_names)
        and all(abs(coefficients[name]) <= 1e-12 for name in active_theory_names)
    ):
        flags.append("THEORY_TERM_ELIMINATED_ALL")
    payload = {
        "model_version": H1_CONTINUOUS_OFI_MODEL_VERSION,
        "regime": REGIME,
        "include_flow_features": any(name in FLOW_FEATURE_NAMES for name in feature_names),
        "hyperparameters": {
            "delta_multiplier": hyperparameters.delta_multiplier,
            "lambda": hyperparameters.lambda_,
            "rho": hyperparameters.rho,
        },
        "intercept": intercept,
        "coefficients": dict(sorted(coefficients.items())),
        "medians": dict(sorted(scaler.medians.items())),
        "iqrs": dict(sorted(scaler.iqrs.items())),
        "zero_variance": scaler.zero_variance_feature_names,
        "train_dates": [sample.trading_date.isoformat() for sample in samples],
        "training_data_hash": _training_data_hash(samples, feature_names),
        "signal": {"k": "2.0", "target_notional_krw": "1000000"},
        "fill": {"entry": ENTRY_FILL_VERSION, "exit": EXIT_FILL_VERSION},
        "cost_contract": "all-required-positive-and-double-stress@1.0.0",
    }
    model_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return SealedRegressionModel(
        include_flow_features=any(name in FLOW_FEATURE_NAMES for name in feature_names),
        hyperparameters=hyperparameters,
        intercept=intercept,
        coefficients=coefficients,
        scaler=scaler,
        sigma_y=sigma_y,
        train_dates=tuple(sample.trading_date for sample in samples),
        flags=tuple(flags),
        model_hash=model_hash,
    )


def _fit_robust_scaler(
    matrix: np.ndarray, feature_names: tuple[str, ...]
) -> tuple[RobustScalerState, np.ndarray]:
    medians = np.median(matrix, axis=0)
    q25 = np.quantile(matrix, 0.25, axis=0)
    q75 = np.quantile(matrix, 0.75, axis=0)
    iqrs = q75 - q25
    active_indices = [index for index, iqr in enumerate(iqrs) if iqr > 0]
    zero_indices = [index for index, iqr in enumerate(iqrs) if iqr <= 0]
    active_names = tuple(feature_names[index] for index in active_indices)
    zero_names = tuple(feature_names[index] for index in zero_indices)
    state = RobustScalerState(
        medians={name: float(medians[index]) for index, name in enumerate(feature_names)},
        iqrs={
            name: float(iqrs[index])
            for index, name in enumerate(feature_names)
            if index in active_indices
        },
        active_feature_names=active_names,
        zero_variance_feature_names=zero_names,
    )
    if not active_indices:
        return state, np.empty((matrix.shape[0], 0), dtype=float)
    return state, (matrix[:, active_indices] - medians[active_indices]) / iqrs[active_indices]


def _training_data_hash(samples: Sequence[RegressionSample], feature_names: tuple[str, ...]) -> str:
    payload = []
    include_flow = any(name in FLOW_FEATURE_NAMES for name in feature_names)
    for sample in samples:
        values = _sample_values(sample, include_flow=include_flow)
        payload.append(
            {
                "date": sample.trading_date.isoformat(),
                "target": _required_target(sample),
                "features": {name: _required_float(values[name]) for name in feature_names},
                "origin": sample.data_origin.value,
            }
        )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _proximal_huber_elastic_net(
    matrix: np.ndarray,
    targets: np.ndarray,
    *,
    delta: float,
    lambda_: float,
    rho: float,
    max_iterations: int = 1500,
    tolerance: float = 1e-8,
) -> tuple[float, np.ndarray]:
    row_count, column_count = matrix.shape
    intercept = float(np.median(targets))
    coefficients = np.zeros(column_count, dtype=float)
    augmented = np.column_stack((np.ones(row_count), matrix))
    smooth_lipschitz = float(np.linalg.eigvalsh(augmented.T @ augmented / row_count)[-1])
    smooth_lipschitz += lambda_ * (1.0 - rho)
    step = 0.95 / max(smooth_lipschitz, 1e-12)
    l1_step = step * lambda_ * rho
    for _ in range(max_iterations):
        residuals = targets - (intercept + matrix @ coefficients)
        psi = np.clip(residuals, -delta, delta)
        new_intercept = intercept + step * float(np.mean(psi))
        gradient = -(matrix.T @ psi) / row_count
        gradient += lambda_ * (1.0 - rho) * coefficients
        candidate = coefficients - step * gradient
        new_coefficients = np.sign(candidate) * np.maximum(np.abs(candidate) - l1_step, 0.0)
        change = max(
            abs(new_intercept - intercept),
            float(np.max(np.abs(new_coefficients - coefficients))) if column_count else 0.0,
        )
        intercept = new_intercept
        coefficients = new_coefficients
        if change <= tolerance:
            break
    coefficients[np.abs(coefficients) <= 1e-12] = 0.0
    return intercept, coefficients


def _huber_values(residuals: np.ndarray, delta: float) -> np.ndarray:
    absolute = np.abs(residuals)
    return np.where(
        absolute <= delta,
        0.5 * residuals**2,
        delta * (absolute - 0.5 * delta),
    )


def _robust_target_scale(targets: np.ndarray) -> float:
    median = float(np.median(targets))
    mad = float(np.median(np.abs(targets - median)))
    return 1.4826 * mad


def _feature_names(
    samples: Sequence[RegressionSample], include_flow_features: bool
) -> tuple[str, ...]:
    fund_ids = sorted({fund_id for sample in samples for fund_id in sample.theoretical_z})
    names = tuple(f"z:{fund_id}" for fund_id in fund_ids)
    if include_flow_features:
        names += FLOW_FEATURE_NAMES
    return names


def _sample_values(
    sample: RegressionSample, *, include_flow: bool
) -> dict[str, Decimal | float | None]:
    values = {f"z:{fund_id}": value for fund_id, value in sample.theoretical_z.items()}
    if include_flow:
        values.update({name: sample.flow_features.get(name) for name in FLOW_FEATURE_NAMES})
    return values


def _usable_samples(
    samples: Sequence[RegressionSample],
    feature_names: tuple[str, ...],
    include_flow: bool,
) -> tuple[tuple[RegressionSample, ...], Counter[str]]:
    usable: list[RegressionSample] = []
    reasons: Counter[str] = Counter()
    for sample in samples:
        if _finite_float(sample.target_return) is None:
            reasons[sample.target_missing_reason or "TARGET_MISSING"] += 1
            continue
        values = _sample_values(sample, include_flow=include_flow)
        missing_names = [name for name in feature_names if _finite_float(values.get(name)) is None]
        if missing_names:
            reasons[f"FEATURE_MISSING:{','.join(missing_names)}"] += 1
            continue
        usable.append(sample)
    return tuple(usable), reasons


def _fit_failure(
    reason: str,
    scheduled_count: int,
    usable_count: int,
    missing_reasons: Mapping[str, int],
) -> RegressionFitResult:
    return RegressionFitResult(
        status=RegressionStatus.NOT_COMPUTABLE,
        model=None,
        reason=reason,
        candidate_scores=(),
        scheduled_sample_count=scheduled_count,
        usable_sample_count=usable_count,
        missing_reason_counts=dict(sorted(missing_reasons.items())),
    )


def _required_target(sample: RegressionSample) -> float:
    value = _finite_float(sample.target_return)
    if value is None:
        raise ValueError("target이 결측이다")
    return value


def _required_float(value: Decimal | float | None) -> float:
    result = _finite_float(value)
    if result is None:
        raise ValueError("결측값을 수치 0으로 대체할 수 없다")
    return result


def _finite_float(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


@dataclass(frozen=True)
class TargetDistributionDiagnostics:
    scheduled_count: int
    computable_count: int
    missing_count: int
    missing_rate: Decimal
    missing_reason_counts: Mapping[str, int]
    mean: Decimal | None
    standard_deviation: Decimal | None
    mad: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    quantiles: Mapping[str, Decimal | None]
    skew: Decimal | None
    excess_kurtosis: Decimal | None


def summarize_target_distribution(
    samples: Sequence[RegressionSample],
) -> TargetDistributionDiagnostics:
    values = [_finite_float(sample.target_return) for sample in samples]
    observed = np.asarray([value for value in values if value is not None], dtype=float)
    missing_reasons = Counter(
        sample.target_missing_reason or "TARGET_MISSING"
        for sample in samples
        if _finite_float(sample.target_return) is None
    )
    scheduled = len(samples)
    missing = scheduled - len(observed)
    quantile_names = ("p01", "p05", "p25", "p50", "p75", "p95", "p99")
    quantile_values = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)
    if not len(observed):
        return TargetDistributionDiagnostics(
            scheduled_count=scheduled,
            computable_count=0,
            missing_count=missing,
            missing_rate=Decimal(missing) / Decimal(scheduled) if scheduled else Decimal("0"),
            missing_reason_counts=dict(sorted(missing_reasons.items())),
            mean=None,
            standard_deviation=None,
            mad=None,
            minimum=None,
            maximum=None,
            quantiles=dict.fromkeys(quantile_names),
            skew=None,
            excess_kurtosis=None,
        )
    mean = float(np.mean(observed))
    std = float(np.std(observed, ddof=1)) if len(observed) > 1 else 0.0
    centered = observed - mean
    skew: float | None = None
    kurtosis: float | None = None
    if len(observed) > 2 and std > 0:
        skew = float(np.mean(centered**3) / std**3)
    if len(observed) > 3 and std > 0:
        kurtosis = float(np.mean(centered**4) / std**4 - 3.0)
    median = float(np.median(observed))
    mad = float(np.median(np.abs(observed - median)))
    quantiles = np.quantile(observed, quantile_values)
    return TargetDistributionDiagnostics(
        scheduled_count=scheduled,
        computable_count=len(observed),
        missing_count=missing,
        missing_rate=Decimal(missing) / Decimal(scheduled) if scheduled else Decimal("0"),
        missing_reason_counts=dict(sorted(missing_reasons.items())),
        mean=Decimal(str(mean)),
        standard_deviation=Decimal(str(std)),
        mad=Decimal(str(mad)),
        minimum=Decimal(str(float(np.min(observed)))),
        maximum=Decimal(str(float(np.max(observed)))),
        quantiles={
            name: Decimal(str(float(value)))
            for name, value in zip(quantile_names, quantiles, strict=True)
        },
        skew=Decimal(str(skew)) if skew is not None else None,
        excess_kurtosis=Decimal(str(kurtosis)) if kurtosis is not None else None,
    )


@dataclass(frozen=True)
class DirectionCalibrationModel:
    method: str
    platt_coefficient: float | None = None
    platt_intercept: float | None = None
    isotonic_x: tuple[float, ...] = ()
    isotonic_y: tuple[float, ...] = ()
    usage: str = CALIBRATION_USAGE

    def predict_probabilities(self, scores: Sequence[Decimal | float]) -> tuple[Decimal, ...]:
        x = np.asarray([float(score) for score in scores], dtype=float)
        if self.method == "platt":
            assert self.platt_coefficient is not None
            assert self.platt_intercept is not None
            logits = np.clip(self.platt_coefficient * x + self.platt_intercept, -40, 40)
            probabilities = 1.0 / (1.0 + np.exp(-logits))
        else:
            probabilities = np.interp(x, self.isotonic_x, self.isotonic_y)
        return tuple(Decimal(str(float(value))) for value in probabilities)


@dataclass(frozen=True)
class CalibrationDiagnostics:
    status: CalibrationStatus
    confidence: DirectionCalibrationModel | None
    brier_score: Decimal | None
    reason: str | None
    usage: str = CALIBRATION_USAGE


@dataclass(frozen=True)
class ReliabilityBin:
    count: int
    mean_probability: Decimal
    observed_positive_rate: Decimal
    minimum_probability: Decimal
    maximum_probability: Decimal


@dataclass(frozen=True)
class CalibrationEvaluation:
    brier_score: Decimal | None
    reliability_curve: tuple[ReliabilityBin, ...]
    reason: str | None
    usage: str = CALIBRATION_USAGE


def fit_direction_calibration(
    train_oof_scores: Sequence[Decimal | float],
    train_targets: Sequence[Decimal | float],
) -> CalibrationDiagnostics:
    """train OOF만 사용하며 신호·사이징·PASS 판정에는 연결하지 않는다."""

    if len(train_oof_scores) != len(train_targets) or len(train_targets) < 10:
        return _calibration_failure("CALIBRATION_SAMPLE_INSUFFICIENT")
    scores = np.asarray([float(value) for value in train_oof_scores], dtype=float)
    labels = np.asarray([float(value) > 0 for value in train_targets], dtype=int)
    if len(np.unique(labels)) != 2:
        return _calibration_failure("CALIBRATION_NOT_IDENTIFIED")
    platt = LogisticRegression(random_state=0, solver="lbfgs")
    platt.fit(scores.reshape(-1, 1), labels)
    platt_probabilities = platt.predict_proba(scores.reshape(-1, 1))[:, 1]
    isotonic = IsotonicRegression(out_of_bounds="clip")
    isotonic_probabilities = isotonic.fit_transform(scores, labels)
    platt_losses = (platt_probabilities - labels) ** 2
    isotonic_losses = (isotonic_probabilities - labels) ** 2
    best_losses = (
        platt_losses
        if float(np.mean(platt_losses)) <= float(np.mean(isotonic_losses))
        else isotonic_losses
    )
    standard_error = (
        float(np.std(best_losses, ddof=1) / math.sqrt(len(best_losses)))
        if len(best_losses) > 1
        else 0.0
    )
    if float(np.mean(platt_losses)) <= float(np.mean(best_losses)) + standard_error:
        model = DirectionCalibrationModel(
            method="platt",
            platt_coefficient=float(platt.coef_[0, 0]),
            platt_intercept=float(np.ravel(platt.intercept_)[0]),
        )
        brier = float(np.mean(platt_losses))
    else:
        model = DirectionCalibrationModel(
            method="isotonic",
            isotonic_x=tuple(float(value) for value in isotonic.X_thresholds_),
            isotonic_y=tuple(float(value) for value in isotonic.y_thresholds_),
        )
        brier = float(np.mean(isotonic_losses))
    return CalibrationDiagnostics(
        status=CalibrationStatus.IDENTIFIED,
        confidence=model,
        brier_score=Decimal(str(brier)),
        reason=None,
    )


def _calibration_failure(reason: str) -> CalibrationDiagnostics:
    return CalibrationDiagnostics(
        status=CalibrationStatus.CALIBRATION_NOT_IDENTIFIED,
        confidence=None,
        brier_score=None,
        reason=reason,
    )


def evaluate_calibration_reliability(
    calibration: DirectionCalibrationModel | None,
    scores: Sequence[Decimal | float],
    targets: Sequence[Decimal | float],
    *,
    maximum_bins: int = 10,
) -> CalibrationEvaluation:
    """validation/test의 adaptive equal-frequency calibration 사후 진단."""

    if calibration is None:
        return CalibrationEvaluation(None, (), "CALIBRATION_NOT_IDENTIFIED")
    if len(scores) != len(targets) or not scores or maximum_bins <= 0:
        return CalibrationEvaluation(None, (), "CALIBRATION_EVALUATION_NOT_COMPUTABLE")
    probabilities = np.asarray(
        [float(value) for value in calibration.predict_probabilities(scores)], dtype=float
    )
    labels = np.asarray([float(value) > 0 for value in targets], dtype=float)
    order = np.argsort(probabilities)
    bin_count = min(maximum_bins, max(1, int(math.sqrt(len(probabilities)))))
    index_bins = np.array_split(order, bin_count)
    reliability: list[ReliabilityBin] = []
    for indices in index_bins:
        if not len(indices):
            continue
        selected_probabilities = probabilities[indices]
        selected_labels = labels[indices]
        reliability.append(
            ReliabilityBin(
                count=len(indices),
                mean_probability=Decimal(str(float(np.mean(selected_probabilities)))),
                observed_positive_rate=Decimal(str(float(np.mean(selected_labels)))),
                minimum_probability=Decimal(str(float(np.min(selected_probabilities)))),
                maximum_probability=Decimal(str(float(np.max(selected_probabilities)))),
            )
        )
    return CalibrationEvaluation(
        brier_score=Decimal(str(float(np.mean((probabilities - labels) ** 2)))),
        reliability_curve=tuple(reliability),
        reason=None,
    )


@dataclass(frozen=True)
class ProgramSemanticsSealEvidence:
    cumulative_buy_sell_monotonic: bool = False
    net_identity_exact: bool = False
    eod_three_way_reconciled: bool = False
    two_day_session_reset_verified: bool = False
    integrated_crosscheck_passed: bool = False

    @property
    def is_sealed(self) -> bool:
        return all(
            (
                self.cumulative_buy_sell_monotonic,
                self.net_identity_exact,
                self.eod_three_way_reconciled,
                self.two_day_session_reset_verified,
                self.integrated_crosscheck_passed,
            )
        )


@dataclass(frozen=True)
class SourceGateEvidence:
    data_origin: FeatureDataOrigin = FeatureDataOrigin.SANITIZED_FIXTURE
    consecutive_probe_days: int = 0
    all_five_raw_feeds_preserved: bool = False
    schema_parse_rate: Decimal | None = None
    field_count_match_rate: Decimal | None = None
    out_of_order_count: int = 0
    disconnect_count: int = 0
    max_clock_error_ms: Decimal | None = None
    all_snapshot_ages_within_2s: bool = False
    depth_complete_rate: Decimal | None = None
    cutoff_depth_complete: bool = False
    quote_and_trade_max_gaps_within_2s: bool = False
    trade_during_quote_gap_count: int = 0
    program_semantics: ProgramSemanticsSealEvidence = ProgramSemanticsSealEvidence()
    venue_mapping_verified: bool = False
    causal_structural_inputs_verified: bool = False
    scheduled_operational_days: int = 0
    eligible_operational_days: int = 0
    raw_normalized_reconciliation_complete: bool = False
    program_window_all_eligible_days: bool = False


@dataclass(frozen=True)
class StageDecision:
    stage: ValidationStage
    verdict: PromotionVerdict
    reasons: tuple[str, ...]
    permutation_p_value: Decimal | None = None
    incremental_expectancy: Decimal | None = None


def evaluate_source_gate(evidence: SourceGateEvidence) -> StageDecision:
    reasons: list[str] = []
    if evidence.data_origin is not FeatureDataOrigin.LIVE_CAPTURE:
        reasons.append("LIVE_SOURCE_NOT_CAPTURED")
    if evidence.consecutive_probe_days < 2:
        reasons.append("SOURCE_PROBE_DAYS_INSUFFICIENT")
    if not evidence.all_five_raw_feeds_preserved:
        reasons.append("RAW_FEEDS_NOT_PRESERVED")
    if evidence.schema_parse_rate != Decimal("1"):
        reasons.append("SCHEMA_PARSE_NOT_100_PERCENT")
    if evidence.field_count_match_rate != Decimal("1"):
        reasons.append("FIELD_COUNT_MATCH_NOT_100_PERCENT")
    if evidence.out_of_order_count or evidence.disconnect_count:
        reasons.append("TRANSPORT_ORDER_OR_DISCONNECT_FAILURE")
    if evidence.max_clock_error_ms is None or evidence.max_clock_error_ms > Decimal("50"):
        reasons.append("CLOCK_SYNC_ERROR")
    if not evidence.all_snapshot_ages_within_2s:
        reasons.append("SNAPSHOT_STALE")
    if evidence.depth_complete_rate is None or evidence.depth_complete_rate < Decimal("0.995"):
        reasons.append("DEPTH_COMPLETENESS_BELOW_99_5_PERCENT")
    if not evidence.cutoff_depth_complete:
        reasons.append("CUTOFF_DEPTH_INCOMPLETE")
    if not evidence.quote_and_trade_max_gaps_within_2s or evidence.trade_during_quote_gap_count:
        reasons.append("PACKET_GAP")
    if not evidence.program_semantics.is_sealed:
        reasons.append("PROGRAM_CROSSCHECK_SEMANTICS_UNRESOLVED")
    if not evidence.venue_mapping_verified:
        reasons.append("PROGRAM_VENUE_MAPPING_UNVERIFIED")
    if not evidence.causal_structural_inputs_verified:
        reasons.append("STRUCTURAL_INPUT_LINEAGE_UNVERIFIED")
    if evidence.scheduled_operational_days < 20 or evidence.eligible_operational_days < 19:
        reasons.append("OPERATING_ELIGIBILITY_BELOW_19_OF_20")
    if not evidence.raw_normalized_reconciliation_complete:
        reasons.append("RAW_NORMALIZED_RECONCILIATION_INCOMPLETE")
    if not evidence.program_window_all_eligible_days:
        reasons.append("PROGRAM_WINDOW_OPERATIONAL_GAP")
    return StageDecision(
        stage=ValidationStage.SOURCE_GATE,
        verdict=PromotionVerdict.HOLD if reasons else PromotionVerdict.PASS,
        reasons=tuple(reasons),
    )


def paired_incremental_permutation_p_value(
    incremental_daily_pnls: Sequence[Decimal], *, permutations: int, seed: int
) -> Decimal:
    if permutations <= 0:
        raise ValueError("permutations는 양수여야 한다")
    if not incremental_daily_pnls:
        return Decimal("1")
    values = np.asarray([float(value) for value in incremental_daily_pnls], dtype=float)
    observed = float(np.mean(values))
    rng = np.random.default_rng(seed)
    count = 0
    for _ in range(permutations):
        signs = rng.choice((-1.0, 1.0), size=len(values))
        if float(np.mean(values * signs)) >= observed:
            count += 1
    return Decimal(count) / Decimal(permutations)


def evaluate_cheap_reject(
    source_gate: StageDecision,
    incremental_validation_pnls: Sequence[Decimal],
    *,
    permutations: int = 1000,
    seed: int = 7,
) -> StageDecision:
    if source_gate.verdict is not PromotionVerdict.PASS:
        return StageDecision(
            stage=ValidationStage.CHEAP_REJECT,
            verdict=PromotionVerdict.HOLD,
            reasons=("SOURCE_GATE_NOT_PASSED",),
        )
    if len(incremental_validation_pnls) < VALIDATION_DAYS:
        return StageDecision(
            stage=ValidationStage.CHEAP_REJECT,
            verdict=PromotionVerdict.HOLD,
            reasons=("VALIDATION_DAYS_INSUFFICIENT",),
        )
    selected = tuple(incremental_validation_pnls[:VALIDATION_DAYS])
    expectancy = sum(selected, Decimal("0")) / Decimal(len(selected))
    p_value = paired_incremental_permutation_p_value(selected, permutations=permutations, seed=seed)
    failures: list[str] = []
    if expectancy <= 0:
        failures.append("INCREMENTAL_NET_EXPECTANCY_NOT_POSITIVE")
    if p_value >= Decimal("0.10"):
        failures.append("VALIDATION_PERMUTATION_P_NOT_BELOW_0_10")
    return StageDecision(
        stage=ValidationStage.CHEAP_REJECT,
        verdict=PromotionVerdict.REJECT if failures else PromotionVerdict.PASS,
        reasons=tuple(failures),
        permutation_p_value=p_value,
        incremental_expectancy=expectancy,
    )


@dataclass(frozen=True)
class RequiredCostEvidence:
    commission_return: Decimal | None
    tax_return: Decimal | None
    spread_return: Decimal | None
    slippage_return: Decimal | None
    market_impact_return: Decimal | None
    product_and_tracking_return: Decimal | None
    product_and_tracking_applicable: bool = False

    @property
    def is_complete(self) -> bool:
        core_complete = all(
            value is not None and value.is_finite() and value > 0 for value in self._core_values()
        )
        if not core_complete:
            return False
        product_cost = self.product_and_tracking_return
        if self.product_and_tracking_applicable:
            return product_cost is not None and product_cost.is_finite() and product_cost > 0
        return product_cost is None

    @property
    def base_total_return(self) -> Decimal | None:
        if not self.is_complete:
            return None
        return sum(
            (
                value
                for value in (*self._core_values(), self.product_and_tracking_return)
                if value is not None
            ),
            Decimal("0"),
        )

    @property
    def double_stress_total_return(self) -> Decimal | None:
        total = self.base_total_return
        return total * Decimal("2") if total is not None else None

    def _core_values(self) -> tuple[Decimal | None, ...]:
        return (
            self.commission_return,
            self.tax_return,
            self.spread_return,
            self.slippage_return,
            self.market_impact_return,
        )


@dataclass(frozen=True)
class ValidationObservation:
    sample: RegressionSample
    estimated_round_trip_cost_return: Decimal | None
    stress_round_trip_cost_return: Decimal | None
    actual_notional: Decimal | None
    long_net_return: Decimal | None
    short_net_return: Decimal | None
    stress_long_net_return: Decimal | None
    stress_short_net_return: Decimal | None
    missing_reason: str | None = None
    required_costs: RequiredCostEvidence | None = None


@dataclass(frozen=True)
class DailyModelOutcome:
    trading_date: date
    signal: SignalDirection | None
    pnl: Decimal
    stress_pnl: Decimal
    eligible_signal: bool
    reason: str | None


def paper_model_outcome(
    model: SealedRegressionModel, observation: ValidationObservation
) -> DailyModelOutcome:
    cost = observation.estimated_round_trip_cost_return
    stress_cost = observation.stress_round_trip_cost_return
    notional = observation.actual_notional
    cost_evidence = observation.required_costs
    if (
        cost_evidence is None
        or not cost_evidence.is_complete
        or cost is None
        or not cost.is_finite()
        or cost <= 0
        or cost != cost_evidence.base_total_return
        or stress_cost is None
        or stress_cost != cost_evidence.double_stress_total_return
        or notional is None
        or notional <= 0
        or notional > TARGET_NOTIONAL_KRW
    ):
        return _no_trade_outcome(observation, "COST_OR_NOTIONAL_NOT_COMPUTABLE")
    prediction = model.predict(observation.sample)
    if prediction is None:
        return _no_trade_outcome(observation, "FEATURE_NOT_COMPUTABLE")
    threshold = SIGNAL_COST_MULTIPLIER * cost
    signal: SignalDirection | None = None
    net_return: Decimal | None = None
    stress_return: Decimal | None = None
    if prediction.gross_return > threshold:
        signal = SignalDirection.LONG
        net_return = observation.long_net_return
        stress_return = observation.stress_long_net_return
    elif prediction.gross_return < -threshold:
        signal = SignalDirection.SHORT
        net_return = observation.short_net_return
        stress_return = observation.stress_short_net_return
    else:
        return _no_trade_outcome(observation, "NO_SIGNAL")
    if net_return is None or stress_return is None:
        return _no_trade_outcome(observation, observation.missing_reason or "FILL_NOT_AVAILABLE")
    return DailyModelOutcome(
        trading_date=observation.sample.trading_date,
        signal=signal,
        pnl=notional * net_return,
        stress_pnl=notional * stress_return,
        eligible_signal=True,
        reason=None,
    )


def _no_trade_outcome(observation: ValidationObservation, reason: str) -> DailyModelOutcome:
    return DailyModelOutcome(
        trading_date=observation.sample.trading_date,
        signal=None,
        pnl=Decimal("0"),
        stress_pnl=Decimal("0"),
        eligible_signal=False,
        reason=reason,
    )


@dataclass(frozen=True)
class PassGateMetrics:
    eligible_trading_days: int
    eligible_signal_count: int
    expectancy: Decimal
    profit_factor: Decimal
    mdd_fraction_of_fixed_capital: Decimal
    top_single_day_positive_profit_share: Decimal
    stress_cumulative_pnl: Decimal
    block_bootstrap_expectancy_ci_lower: Decimal
    permutation_p_value: Decimal
    sealed_test_incremental_net_pnl: Decimal
    sealed_test_complete: bool
    walk_forward_block_count: int


def evaluate_final_pass_gate(
    source_gate: StageDecision,
    cheap_reject: StageDecision,
    metrics: PassGateMetrics,
) -> StageDecision:
    if source_gate.verdict is not PromotionVerdict.PASS:
        return StageDecision(
            ValidationStage.FINAL_PASS_GATE,
            PromotionVerdict.HOLD,
            ("SOURCE_GATE_NOT_PASSED",),
        )
    if cheap_reject.verdict is PromotionVerdict.REJECT:
        return StageDecision(
            ValidationStage.FINAL_PASS_GATE,
            PromotionVerdict.REJECT,
            ("CHEAP_REJECT_FAILED",),
        )
    if cheap_reject.verdict is not PromotionVerdict.PASS:
        return StageDecision(
            ValidationStage.FINAL_PASS_GATE,
            PromotionVerdict.HOLD,
            ("CHEAP_REJECT_NOT_COMPLETED",),
        )
    insufficient: list[str] = []
    if metrics.eligible_trading_days < 120:
        insufficient.append("ELIGIBLE_TRADING_DAYS_BELOW_120")
    if metrics.eligible_signal_count < 30:
        insufficient.append("ELIGIBLE_SIGNALS_BELOW_30")
    if not metrics.sealed_test_complete:
        insufficient.append("SEALED_TEST_NOT_COMPLETE")
    if metrics.walk_forward_block_count < 1:
        insufficient.append("WALK_FORWARD_NOT_COMPLETE")
    if insufficient:
        return StageDecision(
            ValidationStage.FINAL_PASS_GATE,
            PromotionVerdict.HOLD,
            tuple(insufficient),
        )
    failures: list[str] = []
    if metrics.expectancy <= 0:
        failures.append("NET_EXPECTANCY_NOT_POSITIVE")
    if metrics.profit_factor < Decimal("1.2"):
        failures.append("PROFIT_FACTOR_BELOW_1_2")
    if metrics.mdd_fraction_of_fixed_capital > Decimal("0.05"):
        failures.append("MDD_ABOVE_5_PERCENT")
    if metrics.top_single_day_positive_profit_share > Decimal("0.30"):
        failures.append("SINGLE_DAY_PROFIT_SHARE_ABOVE_30_PERCENT")
    if metrics.stress_cumulative_pnl < 0:
        failures.append("DOUBLE_COST_STRESS_PNL_NEGATIVE")
    if metrics.block_bootstrap_expectancy_ci_lower <= 0:
        failures.append("BLOCK_BOOTSTRAP_CI_LOWER_NOT_POSITIVE")
    if metrics.permutation_p_value >= Decimal("0.05"):
        failures.append("PERMUTATION_P_NOT_BELOW_0_05")
    if metrics.sealed_test_incremental_net_pnl <= 0:
        failures.append("SEALED_TEST_INCREMENTAL_NET_PNL_NOT_POSITIVE")
    return StageDecision(
        ValidationStage.FINAL_PASS_GATE,
        PromotionVerdict.REJECT if failures else PromotionVerdict.PASS,
        tuple(failures),
    )


@dataclass(frozen=True)
class ValidationHarnessConfig:
    seed: int = 7
    permutations: int = 1000
    bootstrap_resamples: int = 1000
    bootstrap_block_days: int = 5
    walk_forward_block_days: int = 30

    def __post_init__(self) -> None:
        if self.permutations <= 0 or self.bootstrap_resamples <= 0:
            raise ValueError("permutations와 bootstrap_resamples는 양수여야 한다")
        if self.bootstrap_block_days <= 0:
            raise ValueError("bootstrap_block_days는 양수여야 한다")
        if not 20 <= self.walk_forward_block_days <= 30:
            raise ValueError("walk_forward block은 20~30일이어야 한다")


@dataclass(frozen=True)
class ValidationHarnessResult:
    verdict: PromotionVerdict
    stopped_at: ValidationStage
    source_gate: StageDecision
    cheap_reject: StageDecision | None
    final_gate: StageDecision | None
    full_model_hash: str | None
    theoretical_model_hash: str | None
    train_diagnostics: TargetDistributionDiagnostics | None
    validation_diagnostics: TargetDistributionDiagnostics | None
    test_diagnostics: TargetDistributionDiagnostics | None
    metrics: PassGateMetrics | None
    flags: tuple[str, ...]
    live_validation_status: str
    walk_forward_diagnostics: tuple[TargetDistributionDiagnostics, ...] = ()


def run_h1_continuous_ofi_validation(
    observations: Sequence[ValidationObservation],
    source_evidence: SourceGateEvidence,
    config: ValidationHarnessConfig | None = None,
) -> ValidationHarnessResult:
    """source→60 train→30 validation→30 sealed test→walk-forward를 fail-closed 실행한다."""

    resolved_config = config or ValidationHarnessConfig()
    source_gate = evaluate_source_gate(source_evidence)
    if source_gate.verdict is not PromotionVerdict.PASS:
        return _held_harness_result(source_gate)
    ordered = tuple(sorted(observations, key=lambda item: item.sample.trading_date))
    if len({item.sample.trading_date for item in ordered}) != len(ordered):
        return _held_harness_result(source_gate, "DUPLICATE_TRADING_DATE")
    if any(item.sample.data_origin is not FeatureDataOrigin.LIVE_CAPTURE for item in ordered):
        return _held_harness_result(source_gate, "FIXTURE_OR_NON_LIVE_SAMPLE_PRESENT")
    if len(ordered) < INITIAL_TRAIN_DAYS + VALIDATION_DAYS:
        return _held_harness_result(source_gate, "TRAIN_VALIDATION_DAYS_INSUFFICIENT")

    train = ordered[:INITIAL_TRAIN_DAYS]
    validation = ordered[INITIAL_TRAIN_DAYS : INITIAL_TRAIN_DAYS + VALIDATION_DAYS]
    full_fit = fit_sealed_huber_elastic_net(
        [item.sample for item in train], include_flow_features=True
    )
    baseline_fit = fit_sealed_huber_elastic_net(
        [item.sample for item in train], include_flow_features=False
    )
    train_diagnostics = summarize_target_distribution([item.sample for item in train])
    validation_diagnostics = summarize_target_distribution([item.sample for item in validation])
    if full_fit.model is None or baseline_fit.model is None:
        flags = tuple(
            reason for reason in (full_fit.reason, baseline_fit.reason) if reason is not None
        )
        return ValidationHarnessResult(
            verdict=PromotionVerdict.HOLD,
            stopped_at=ValidationStage.TRAIN_ONLY_SELECTION,
            source_gate=source_gate,
            cheap_reject=None,
            final_gate=None,
            full_model_hash=None,
            theoretical_model_hash=None,
            train_diagnostics=train_diagnostics,
            validation_diagnostics=validation_diagnostics,
            test_diagnostics=None,
            metrics=None,
            flags=flags,
            live_validation_status="HOLD/TRAIN_NOT_COMPUTABLE",
        )
    full_model = full_fit.model
    baseline_model = baseline_fit.model
    full_validation = tuple(paper_model_outcome(full_model, item) for item in validation)
    baseline_validation = tuple(paper_model_outcome(baseline_model, item) for item in validation)
    incremental_validation = tuple(
        full.pnl - baseline.pnl
        for full, baseline in zip(full_validation, baseline_validation, strict=True)
    )
    cheap = evaluate_cheap_reject(
        source_gate,
        incremental_validation,
        permutations=resolved_config.permutations,
        seed=resolved_config.seed,
    )
    shared_flags = tuple(dict.fromkeys((*full_model.flags, *baseline_model.flags)))
    if cheap.verdict is not PromotionVerdict.PASS:
        return ValidationHarnessResult(
            verdict=cheap.verdict,
            stopped_at=ValidationStage.CHEAP_REJECT,
            source_gate=source_gate,
            cheap_reject=cheap,
            final_gate=None,
            full_model_hash=full_model.model_hash,
            theoretical_model_hash=baseline_model.model_hash,
            train_diagnostics=train_diagnostics,
            validation_diagnostics=validation_diagnostics,
            test_diagnostics=None,
            metrics=None,
            flags=shared_flags,
            live_validation_status=f"{cheap.verdict.value}/CHEAP_REJECT",
        )
    if len(ordered) < INITIAL_TRAIN_DAYS + VALIDATION_DAYS + SEALED_TEST_DAYS:
        return ValidationHarnessResult(
            verdict=PromotionVerdict.HOLD,
            stopped_at=ValidationStage.SEALED_TEST,
            source_gate=source_gate,
            cheap_reject=cheap,
            final_gate=None,
            full_model_hash=full_model.model_hash,
            theoretical_model_hash=baseline_model.model_hash,
            train_diagnostics=train_diagnostics,
            validation_diagnostics=validation_diagnostics,
            test_diagnostics=None,
            metrics=None,
            flags=shared_flags,
            live_validation_status="HOLD/SEALED_TEST_NOT_COLLECTED",
        )
    test_start = INITIAL_TRAIN_DAYS + VALIDATION_DAYS
    test_end = test_start + SEALED_TEST_DAYS
    sealed_test = ordered[test_start:test_end]
    test_diagnostics = summarize_target_distribution([item.sample for item in sealed_test])
    test_full = tuple(paper_model_outcome(full_model, item) for item in sealed_test)
    test_baseline = tuple(paper_model_outcome(baseline_model, item) for item in sealed_test)
    sealed_incremental = sum(
        (full.pnl - baseline.pnl for full, baseline in zip(test_full, test_baseline, strict=True)),
        Decimal("0"),
    )

    walk_outcomes: list[DailyModelOutcome] = []
    walk_diagnostics: list[TargetDistributionDiagnostics] = []
    cursor = test_end
    walk_blocks = 0
    while len(ordered) - cursor >= 20:
        block_size = min(resolved_config.walk_forward_block_days, len(ordered) - cursor)
        if block_size < 20:
            break
        expanding = ordered[:cursor]
        fold_fit = fit_sealed_huber_elastic_net(
            [item.sample for item in expanding], include_flow_features=True
        )
        if fold_fit.model is None:
            break
        block = ordered[cursor : cursor + block_size]
        walk_diagnostics.append(summarize_target_distribution([item.sample for item in block]))
        walk_outcomes.extend(paper_model_outcome(fold_fit.model, item) for item in block)
        walk_blocks += 1
        cursor += block_size

    combined = (*test_full, *walk_outcomes)
    pnls = tuple(item.pnl for item in combined)
    stress_pnls = tuple(item.stress_pnl for item in combined)
    metrics = _pass_metrics(
        pnls,
        stress_pnls,
        eligible_days=len(ordered[:cursor]),
        eligible_signal_count=sum(item.eligible_signal for item in combined),
        sealed_incremental=sealed_incremental,
        walk_blocks=walk_blocks,
        config=resolved_config,
    )
    final = evaluate_final_pass_gate(source_gate, cheap, metrics)
    return ValidationHarnessResult(
        verdict=final.verdict,
        stopped_at=ValidationStage.FINAL_PASS_GATE,
        source_gate=source_gate,
        cheap_reject=cheap,
        final_gate=final,
        full_model_hash=full_model.model_hash,
        theoretical_model_hash=baseline_model.model_hash,
        train_diagnostics=train_diagnostics,
        validation_diagnostics=validation_diagnostics,
        test_diagnostics=test_diagnostics,
        metrics=metrics,
        flags=shared_flags,
        live_validation_status=f"{final.verdict.value}/LIVE_VALIDATION",
        walk_forward_diagnostics=tuple(walk_diagnostics),
    )


def _held_harness_result(
    source_gate: StageDecision, extra_reason: str | None = None
) -> ValidationHarnessResult:
    reasons = source_gate.reasons
    if extra_reason is not None:
        reasons = (*reasons, extra_reason)
    held_source = StageDecision(
        stage=ValidationStage.SOURCE_GATE,
        verdict=PromotionVerdict.HOLD,
        reasons=reasons,
    )
    return ValidationHarnessResult(
        verdict=PromotionVerdict.HOLD,
        stopped_at=ValidationStage.SOURCE_GATE,
        source_gate=held_source,
        cheap_reject=None,
        final_gate=None,
        full_model_hash=None,
        theoretical_model_hash=None,
        train_diagnostics=None,
        validation_diagnostics=None,
        test_diagnostics=None,
        metrics=None,
        flags=(),
        live_validation_status="HOLD/LIVE_SOURCE_NOT_CAPTURED",
    )


def _pass_metrics(
    pnls: Sequence[Decimal],
    stress_pnls: Sequence[Decimal],
    *,
    eligible_days: int,
    eligible_signal_count: int,
    sealed_incremental: Decimal,
    walk_blocks: int,
    config: ValidationHarnessConfig,
) -> PassGateMetrics:
    trades = tuple(value for value in pnls if value != 0)
    expectancy = sum(trades, Decimal("0")) / Decimal(len(trades)) if trades else Decimal("0")
    gross_profit = sum((value for value in trades if value > 0), Decimal("0"))
    gross_loss = sum((-value for value in trades if value < 0), Decimal("0"))
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else Decimal("Infinity")
        if gross_profit > 0
        else Decimal("0")
    )
    cumulative = Decimal("0")
    peak = RESEARCH_CAPITAL_KRW
    max_drawdown = Decimal("0")
    positive = [value for value in pnls if value > 0]
    for pnl in pnls:
        cumulative += pnl
        equity = RESEARCH_CAPITAL_KRW + cumulative
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    positive_total = sum(positive, Decimal("0"))
    concentration = max(positive) / positive_total if positive_total > 0 else Decimal("0")
    ci_lower, _ = block_bootstrap_expectancy_ci(
        pnls,
        resamples=config.bootstrap_resamples,
        block_days=config.bootstrap_block_days,
        seed=config.seed,
    )
    permutation = paired_incremental_permutation_p_value(
        pnls, permutations=config.permutations, seed=config.seed
    )
    return PassGateMetrics(
        eligible_trading_days=eligible_days,
        eligible_signal_count=eligible_signal_count,
        expectancy=expectancy,
        profit_factor=profit_factor,
        mdd_fraction_of_fixed_capital=max_drawdown / RESEARCH_CAPITAL_KRW,
        top_single_day_positive_profit_share=concentration,
        stress_cumulative_pnl=sum(stress_pnls, Decimal("0")),
        block_bootstrap_expectancy_ci_lower=ci_lower,
        permutation_p_value=permutation,
        sealed_test_incremental_net_pnl=sealed_incremental,
        sealed_test_complete=True,
        walk_forward_block_count=walk_blocks,
    )


def block_bootstrap_expectancy_ci(
    daily_pnls: Sequence[Decimal],
    *,
    resamples: int,
    block_days: int,
    seed: int,
    confidence: float = 0.95,
) -> tuple[Decimal, Decimal]:
    if resamples <= 0 or block_days <= 0:
        raise ValueError("resamples와 block_days는 양수여야 한다")
    if not daily_pnls:
        return Decimal("0"), Decimal("0")
    values = np.asarray([float(value) for value in daily_pnls], dtype=float)
    block_size = min(block_days, len(values))
    starts = np.arange(0, len(values) - block_size + 1)
    rng = np.random.default_rng(seed)
    means = np.empty(resamples, dtype=float)
    blocks_needed = math.ceil(len(values) / block_size)
    for index in range(resamples):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([values[start : start + block_size] for start in chosen])
        means[index] = float(np.mean(sample[: len(values)]))
    alpha = (1.0 - confidence) / 2.0
    return (
        Decimal(str(float(np.quantile(means, alpha)))),
        Decimal(str(float(np.quantile(means, 1.0 - alpha)))),
    )
