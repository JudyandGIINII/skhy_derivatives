"""거래 비용 계산과 실험 비용모델 완전성 gate (PRD 10.4, 14.3).

각 비용 항목을 분리해 기본 비용과 2배 스트레스 비용을 동시에 낼 수 있게 한다.
슬리피지는 이 최소판에서는 체결 시점에(`fill_model.try_fill_leg`) 실측되므로
여기서는 0으로 두고, 사전 추정 단계(Signal.expected_cost)에서는 스프레드·
수수료·세금·시장충격만 반영한다.

``estimate_transaction_cost``는 한 체결의 계산 primitive다. 실험을 시작하거나 결과를
승격 경로에 넣기 전에는 반드시 ``validate_experiment_cost_model``을 호출해 전략별 필수
항목의 누락·0 값과 mutation gate를 통과해야 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class CostComponent(StrEnum):
    COMMISSION = "commission"
    TAX = "tax"
    SPREAD = "spread"
    SLIPPAGE = "slippage"
    MARKET_IMPACT = "market_impact"
    FX = "fx"
    ADR_ISSUANCE_CANCELLATION = "adr_issuance_cancellation"
    BORROW = "borrow"
    PRODUCT_FEES_TRACKING = "product_fees_tracking"


class StrategyCostProfile(StrEnum):
    H1 = "H1"
    H2 = "H2"
    H3 = "H3"


_COMMON_COMPONENTS = frozenset(
    {
        CostComponent.COMMISSION,
        CostComponent.TAX,
        CostComponent.SPREAD,
        CostComponent.SLIPPAGE,
        CostComponent.MARKET_IMPACT,
    }
)
_REQUIRED_COMPONENTS = {
    StrategyCostProfile.H1: _COMMON_COMPONENTS | {CostComponent.PRODUCT_FEES_TRACKING},
    StrategyCostProfile.H2: _COMMON_COMPONENTS
    | {
        CostComponent.FX,
        CostComponent.ADR_ISSUANCE_CANCELLATION,
        CostComponent.BORROW,
    },
    # H3는 어느 시장을 실행 다리로 택하든 동일 manifest를 사용하므로 USD/KRW와
    # 양방향(숏 포함) 비용을 보수적으로 필수화한다.
    StrategyCostProfile.H3: _COMMON_COMPONENTS | {CostComponent.FX, CostComponent.BORROW},
}


class CostModelCompletenessError(ValueError):
    """필수 비용 항목이 없거나 양수가 아닐 때 실험을 중단한다."""


class CostModelMutationError(RuntimeError):
    """필수 항목 삭제/0 mutation을 검증기가 놓칠 때 발생한다."""


@dataclass(frozen=True)
class CostModelValidationReport:
    strategy_id: str
    profile: StrategyCostProfile
    required_components: tuple[CostComponent, ...]
    configured_components: tuple[CostComponent, ...]
    mutation_count: int


def _strategy_profile(strategy_id: str) -> StrategyCostProfile:
    normalized = strategy_id.strip().lower()
    for prefix, profile in (
        ("h1", StrategyCostProfile.H1),
        ("h2", StrategyCostProfile.H2),
        ("h3", StrategyCostProfile.H3),
    ):
        if normalized == prefix or normalized.startswith(f"{prefix}_") or normalized.startswith(
            f"{prefix}-"
        ):
            return profile
    raise CostModelCompletenessError(
        f"알 수 없는 전략 '{strategy_id}'의 필수 비용항목을 추론할 수 없어 실험을 차단함"
    )


def required_cost_components(strategy_id: str) -> frozenset[CostComponent]:
    """전략 계열별 PRD 10.4 필수 비용 항목을 반환한다."""

    return _REQUIRED_COMPONENTS[_strategy_profile(strategy_id)]


def _normalize_component_values[ComponentKey: str](
    component_values: Mapping[ComponentKey, Decimal],
) -> dict[CostComponent, Decimal]:
    normalized: dict[CostComponent, Decimal] = {}
    for raw_component, raw_value in component_values.items():
        try:
            component = CostComponent(raw_component)
        except ValueError:
            continue
        try:
            normalized[component] = Decimal(raw_value)
        except (InvalidOperation, TypeError, ValueError):
            normalized[component] = Decimal("NaN")
    return normalized


def _completeness_errors(
    required: frozenset[CostComponent], values: Mapping[CostComponent, Decimal]
) -> tuple[tuple[CostComponent, ...], tuple[CostComponent, ...]]:
    missing = tuple(sorted(required - values.keys(), key=str))
    non_positive = tuple(
        sorted(
            (
                component
                for component in required & values.keys()
                if not values[component].is_finite() or values[component] <= 0
            ),
            key=str,
        )
    )
    return missing, non_positive


def validate_cost_model_completeness[ComponentKey: str](
    strategy_id: str,
    component_values: Mapping[ComponentKey, Decimal],
) -> CostModelValidationReport:
    """전략별 필수 항목의 누락·0·음수·비유한 값을 fail-closed로 거부한다."""

    profile = _strategy_profile(strategy_id)
    required = _REQUIRED_COMPONENTS[profile]
    values = _normalize_component_values(component_values)
    missing, non_positive = _completeness_errors(required, values)
    if missing or non_positive:
        details: list[str] = []
        if missing:
            details.append("누락=" + ",".join(component.value for component in missing))
        if non_positive:
            details.append("0/음수/비유한=" + ",".join(component.value for component in non_positive))
        raise CostModelCompletenessError(
            f"{strategy_id} 비용모델 불완전: {'; '.join(details)}"
        )
    configured = tuple(sorted(values.keys(), key=str))
    return CostModelValidationReport(
        strategy_id=strategy_id,
        profile=profile,
        required_components=tuple(sorted(required, key=str)),
        configured_components=configured,
        mutation_count=0,
    )


def validate_experiment_cost_model[ComponentKey: str](
    strategy_id: str,
    component_values: Mapping[ComponentKey, Decimal],
) -> CostModelValidationReport:
    """실험 진입 gate: 완전성 확인 후 모든 필수 삭제/0 mutation이 실패함을 확인한다."""

    baseline = validate_cost_model_completeness(strategy_id, component_values)
    normalized = _normalize_component_values(component_values)
    required = frozenset(baseline.required_components)
    mutation_count = 0
    for component in baseline.required_components:
        missing_mutation = dict(normalized)
        del missing_mutation[component]
        missing, non_positive = _completeness_errors(required, missing_mutation)
        mutation_count += 1
        if not missing and not non_positive:
            raise CostModelMutationError(f"필수 비용 삭제 mutation을 놓침: {component.value}")

        zero_mutation = dict(normalized)
        zero_mutation[component] = Decimal("0")
        missing, non_positive = _completeness_errors(required, zero_mutation)
        mutation_count += 1
        if not missing and not non_positive:
            raise CostModelMutationError(f"필수 비용 0 mutation을 놓침: {component.value}")

    return CostModelValidationReport(
        strategy_id=baseline.strategy_id,
        profile=baseline.profile,
        required_components=baseline.required_components,
        configured_components=baseline.configured_components,
        mutation_count=mutation_count,
    )


@dataclass(frozen=True)
class CostBreakdown:
    commission: Decimal
    tax: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    market_impact_cost: Decimal

    @property
    def total(self) -> Decimal:
        return (
            self.commission
            + self.tax
            + self.spread_cost
            + self.slippage_cost
            + self.market_impact_cost
        )

    def stressed(self, multiplier: Decimal) -> CostBreakdown:
        if multiplier <= 0:
            raise ValueError("비용 stress multiplier는 양수여야 한다")
        return CostBreakdown(
            commission=self.commission * multiplier,
            tax=self.tax * multiplier,
            spread_cost=self.spread_cost * multiplier,
            slippage_cost=self.slippage_cost * multiplier,
            market_impact_cost=self.market_impact_cost * multiplier,
        )


@dataclass(frozen=True)
class CostModelParams:
    commission_rate: Decimal  # 매매대금 대비 비율
    tax_rate: Decimal  # 매도 시 증권거래세 등, 매매대금 대비 비율
    market_impact_coefficient: Decimal  # sqrt(참여율) 시장충격 계수

    def __post_init__(self) -> None:
        for name, value in (
            ("commission_rate", self.commission_rate),
            ("tax_rate", self.tax_rate),
            ("market_impact_coefficient", self.market_impact_coefficient),
        ):
            if not value.is_finite() or value <= 0:
                raise CostModelCompletenessError(f"{name}은 양수인 유한 Decimal이어야 한다")


def estimate_transaction_cost(
    bid_price: Decimal,
    ask_price: Decimal,
    order_quantity: Decimal,
    quote_depth: Decimal,
    params: CostModelParams,
    is_sell: bool,
) -> CostBreakdown:
    if order_quantity < 0:
        raise ValueError("order_quantity는 음수일 수 없다")
    if bid_price < 0 or ask_price < 0:
        raise ValueError("bid_price와 ask_price는 음수일 수 없다")
    if bid_price > ask_price:
        raise ValueError("bid_price는 ask_price를 초과할 수 없다")
    if quote_depth < 0:
        raise ValueError("quote_depth는 음수일 수 없다")

    mid_price = (bid_price + ask_price) / 2
    notional = mid_price * order_quantity

    commission = notional * params.commission_rate
    tax = notional * params.tax_rate if is_sell else Decimal("0")

    half_spread = (ask_price - bid_price) / 2
    spread_cost = half_spread * order_quantity

    participation = order_quantity / quote_depth if quote_depth > 0 else Decimal("1")
    market_impact_cost = (
        params.market_impact_coefficient * participation.sqrt() * mid_price * order_quantity
    )

    return CostBreakdown(
        commission=commission,
        tax=tax,
        spread_cost=spread_cost,
        slippage_cost=Decimal("0"),
        market_impact_cost=market_impact_cost,
    )
