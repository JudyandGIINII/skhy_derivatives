"""KIS 주소스·Toss 대조·KRX 직전일 참조로 만드는 원래 H1 15:10 feature."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from skhy_research.application.leverage_universe_discovery import DiscoveredLeveragedProduct
from skhy_research.domain.enums import AssetClass, MarketDataFeedMode, QualityFlag
from skhy_research.domain.market import IndicativeValueKind, MarketPriceSnapshot
from skhy_research.features.h1_close_pressure.close_pressure import (
    ORIGINAL_H1_LIVE_DATA_RESOLUTION,
    ORIGINAL_H1_PROMOTION_SCOPE,
    ClosePressureResult,
    FundContribution,
    estimated_close_pressure,
)
from skhy_research.features.h1_close_pressure.theoretical_exposure import (
    theoretical_delta_exposure,
)
from skhy_research.ports.market_data import (
    MarketDataSnapshotProvider,
    MarketSnapshotBatch,
    MarketSnapshotTarget,
)
from skhy_research.strategies.h1_close_rebalance.decision_window import (
    H1DecisionWindow,
    assert_live_decision_time,
)
from skhy_research.strategies.h1_close_rebalance.lookahead_guard import assert_no_lookahead

H1_LIVE_MODEL_VERSION = "h1_kis_live_listed_notional_reduced_v1"
_H1_UNDERLYING_TARGETS = (
    MarketSnapshotTarget("KRX_000660_COMMON_STOCK", "000660", AssetClass.COMMON_STOCK),
    MarketSnapshotTarget("KRX_005930_COMMON_STOCK", "005930", AssetClass.COMMON_STOCK),
)


class H1LiveInputError(ValueError):
    """live H1 계약이 결측·중복·잘못된 단위를 포함할 때."""


class H1LiveSnapshotBlockedError(RuntimeError):
    """stale·source divergence·모의 feed가 있어 신호 생성을 차단할 때."""

    def __init__(self, evaluations: tuple[LiveSnapshotQuality, ...]) -> None:
        self.evaluations = evaluations
        blocked = [item.instrument_id for item in evaluations if item.blocks_signal]
        super().__init__(f"live H1 snapshot quality gate 차단: {blocked}")


@dataclass(frozen=True)
class H1LiveFundInput:
    fund_id: str
    beta: Decimal
    listed_notional_proxy: Decimal
    kappa: Decimal
    observable_flow_adjustment: Decimal | None
    basis_date: date
    available_at_utc: int
    input_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class KrxPreviousCloseReference:
    instrument_id: str
    basis_date: date
    previous_close: Decimal
    received_at_utc: int
    input_record_id: str
    max_live_move_pct: Decimal


@dataclass(frozen=True)
class LiveSnapshotQuality:
    instrument_id: str
    flags: frozenset[QualityFlag]
    is_live_primary: bool
    blocks_signal: bool
    live_vs_krx_move_pct: Decimal
    primary_vs_secondary_divergence_pct: Decimal | None
    source_time_skew_ns: int | None


@dataclass(frozen=True)
class H1LiveIndicativeValueEvidence:
    fund_id: str
    value: Decimal
    kind: IndicativeValueKind
    observed_at_utc: int
    published_at_utc: int
    consumed_by_close_pressure: bool = False


@dataclass(frozen=True)
class H1LiveFundFeature:
    fund_id: str
    beta: Decimal
    listed_notional_proxy: Decimal
    theoretical_delta_exposure: Decimal
    kappa: Decimal


@dataclass(frozen=True)
class H1LiveFeatureSet:
    trading_date: date
    decision_time_utc: int
    underlying_instrument_id: str
    underlying_intraday_return: Decimal
    underlying_20d_adv_notional: Decimal
    fund_features: tuple[H1LiveFundFeature, ...]
    indicative_value_evidence: tuple[H1LiveIndicativeValueEvidence, ...]
    snapshot_quality: tuple[LiveSnapshotQuality, ...]
    close_pressure: ClosePressureResult
    live_snapshots_used: tuple[MarketPriceSnapshot, ...]
    input_record_ids: tuple[str, ...]
    model_version: str = H1_LIVE_MODEL_VERSION
    data_resolution: str = ORIGINAL_H1_LIVE_DATA_RESOLUTION
    promotion_scope: str = ORIGINAL_H1_PROMOTION_SCOPE
    promotion_eligible: bool = True


@dataclass(frozen=True)
class H1LiveSnapshotCollection:
    targets: tuple[MarketSnapshotTarget, ...]
    primary_batch: MarketSnapshotBatch
    secondary_batch: MarketSnapshotBatch

    @property
    def decision_time_utc(self) -> int:
        """두 feed가 모두 가용해진 시각. 예정 15:10을 가용시각으로 위장하지 않는다."""

        return max(self.primary_batch.received_at_utc, self.secondary_batch.received_at_utc)


def build_h1_live_snapshot_targets(
    products: tuple[DiscoveredLeveragedProduct, ...],
) -> tuple[MarketSnapshotTarget, ...]:
    """2개 기초주식과 당일 KRX에서 발견한 ETF/ETN universe를 결합한다."""

    targets = list(_H1_UNDERLYING_TARGETS)
    targets.extend(
        MarketSnapshotTarget(item.instrument_id, item.source_symbol, item.asset_class)
        for item in products
    )
    instrument_ids = [item.instrument_id for item in targets]
    symbols = [item.symbol for item in targets]
    if len(set(instrument_ids)) != len(instrument_ids) or len(set(symbols)) != len(symbols):
        raise H1LiveInputError("H1 live snapshot target에 중복된 종목이 있다")
    return tuple(targets)


def collect_h1_live_snapshots(
    products: tuple[DiscoveredLeveragedProduct, ...],
    primary_provider: MarketDataSnapshotProvider,
    secondary_provider: MarketDataSnapshotProvider,
    *,
    decision_window: H1DecisionWindow,
) -> H1LiveSnapshotCollection:
    """15:10 스케줄 시각을 요청 as-of로 쓰되 실제 decision은 수신 후에 잡는다."""

    targets = build_h1_live_snapshot_targets(products)
    primary = primary_provider.get_price_snapshots(
        targets,
        requested_as_of_utc=decision_window.signal_snapshot_utc,
    )
    secondary = secondary_provider.get_price_snapshots(
        targets,
        requested_as_of_utc=decision_window.signal_snapshot_utc,
    )
    return H1LiveSnapshotCollection(targets, primary, secondary)


def build_h1_live_feature(
    fund_inputs: list[H1LiveFundInput],
    primary_batch: MarketSnapshotBatch,
    secondary_batch: MarketSnapshotBatch,
    krx_references: list[KrxPreviousCloseReference],
    *,
    underlying_instrument_id: str,
    underlying_20d_adv_notional: Decimal,
    trading_date: date,
    decision_window: H1DecisionWindow,
    decision_time_utc: int,
    max_snapshot_age_ns: int,
    max_source_time_skew_ns: int,
    max_cross_source_divergence_pct: Decimal,
) -> H1LiveFeatureSet:
    """Guard를 모두 통과한 15:10 스냅샷만 원래 H1 scope로 보낸다."""

    _validate_scalar_inputs(
        fund_inputs,
        underlying_20d_adv_notional,
        max_snapshot_age_ns,
        max_source_time_skew_ns,
        max_cross_source_divergence_pct,
    )
    assert_live_decision_time(decision_window, decision_time_utc)
    if primary_batch.provider_name != "kis" or secondary_batch.provider_name != "toss":
        raise H1LiveInputError("live H1은 KIS 주소스·Toss 대조 batch를 요구한다")
    if primary_batch.requested_as_of_utc != decision_window.signal_snapshot_utc:
        raise H1LiveInputError("KIS requested_as_of가 H1 15:10 snapshot 시각과 다르다")
    if secondary_batch.requested_as_of_utc != decision_window.signal_snapshot_utc:
        raise H1LiveInputError("Toss requested_as_of가 H1 15:10 snapshot 시각과 다르다")

    primary = _snapshot_map(primary_batch)
    secondary = _snapshot_map(secondary_batch)
    references = _reference_map(krx_references)
    if set(primary) != set(secondary):
        raise H1LiveInputError("KIS·Toss snapshot universe가 다르다")
    if set(primary) != set(references):
        raise H1LiveInputError("live snapshot·KRX 직전일 종가 universe가 다르다")
    if underlying_instrument_id not in primary:
        raise H1LiveInputError("기초자산 live snapshot이 없다")

    all_snapshots = list(primary_batch.snapshots + secondary_batch.snapshots)
    assert_no_lookahead([], decision_time_utc, all_snapshots)
    evaluations = tuple(
        evaluate_live_snapshot_quality(
            primary[instrument_id],
            secondary[instrument_id],
            references[instrument_id],
            decision_time_utc=decision_time_utc,
            max_snapshot_age_ns=max_snapshot_age_ns,
            max_source_time_skew_ns=max_source_time_skew_ns,
            max_cross_source_divergence_pct=max_cross_source_divergence_pct,
        )
        for instrument_id in sorted(primary)
    )
    if any(item.blocks_signal for item in evaluations):
        raise H1LiveSnapshotBlockedError(evaluations)

    underlying_reference = references[underlying_instrument_id]
    _assert_reference_available(underlying_reference, trading_date, decision_time_utc)
    underlying_return = (
        primary[underlying_instrument_id].last_price / underlying_reference.previous_close
        - Decimal("1")
    )

    contributions: list[FundContribution] = []
    fund_features: list[H1LiveFundFeature] = []
    lineage = _LineageIds()
    for snapshot in all_snapshots:
        lineage.add(snapshot.record_id)
    for reference in krx_references:
        _assert_reference_available(reference, trading_date, decision_time_utc)
        lineage.add(reference.input_record_id)

    seen_funds: set[str] = set()
    for item in fund_inputs:
        _assert_fund_available(item, trading_date, decision_time_utc)
        if item.fund_id in seen_funds:
            raise H1LiveInputError(f"fund_id 중복: {item.fund_id}")
        if item.fund_id not in primary:
            raise H1LiveInputError(f"fund_id={item.fund_id}의 live snapshot이 없다")
        seen_funds.add(item.fund_id)
        for record_id in item.input_record_ids:
            lineage.add(record_id)
        exposure = theoretical_delta_exposure(
            item.beta,
            item.listed_notional_proxy,
            underlying_return,
        )
        fund_features.append(
            H1LiveFundFeature(
                fund_id=item.fund_id,
                beta=item.beta,
                listed_notional_proxy=item.listed_notional_proxy,
                theoretical_delta_exposure=exposure,
                kappa=item.kappa,
            )
        )
        contributions.append(
            FundContribution(
                fund_id=item.fund_id,
                theoretical_delta_exposure=exposure,
                kappa=item.kappa,
                observable_flow_adjustment=item.observable_flow_adjustment,
            )
        )

    base_pressure = estimated_close_pressure(contributions, underlying_20d_adv_notional)
    close_pressure = ClosePressureResult(
        value=base_pressure.value,
        model_version=H1_LIVE_MODEL_VERSION,
        missing_flow_fund_ids=base_pressure.missing_flow_fund_ids,
        data_resolution=ORIGINAL_H1_LIVE_DATA_RESOLUTION,
        promotion_scope=ORIGINAL_H1_PROMOTION_SCOPE,
        promotion_eligible=True,
    )
    indicative_evidence = tuple(
        H1LiveIndicativeValueEvidence(
            fund_id=item.instrument_id,
            value=item.indicative_value,
            kind=item.indicative_value_kind,
            observed_at_utc=item.indicative_value_observed_at_utc,
            published_at_utc=item.published_time_utc,
        )
        for item in primary_batch.snapshots
        if item.instrument_id in seen_funds
        and item.indicative_value is not None
        and item.indicative_value_kind is not None
        and item.indicative_value_observed_at_utc is not None
    )
    return H1LiveFeatureSet(
        trading_date=trading_date,
        decision_time_utc=decision_time_utc,
        underlying_instrument_id=underlying_instrument_id,
        underlying_intraday_return=underlying_return,
        underlying_20d_adv_notional=underlying_20d_adv_notional,
        fund_features=tuple(fund_features),
        indicative_value_evidence=indicative_evidence,
        snapshot_quality=evaluations,
        close_pressure=close_pressure,
        live_snapshots_used=tuple(all_snapshots),
        input_record_ids=lineage.values,
    )


def evaluate_live_snapshot_quality(
    primary: MarketPriceSnapshot,
    secondary: MarketPriceSnapshot,
    reference: KrxPreviousCloseReference,
    *,
    decision_time_utc: int,
    max_snapshot_age_ns: int,
    max_source_time_skew_ns: int,
    max_cross_source_divergence_pct: Decimal,
) -> LiveSnapshotQuality:
    if primary.instrument_id != secondary.instrument_id:
        raise H1LiveInputError("서로 다른 instrument_id는 대조할 수 없다")
    if primary.instrument_id != reference.instrument_id:
        raise H1LiveInputError("live snapshot과 KRX reference instrument_id가 다르다")
    flags = set(primary.quality_flag)
    flags.update(secondary.quality_flag)
    if decision_time_utc - primary.event_time_utc > max_snapshot_age_ns:
        flags.add(QualityFlag.STALE)
    if decision_time_utc - secondary.event_time_utc > max_snapshot_age_ns:
        flags.add(QualityFlag.STALE)
    time_skew = abs(primary.event_time_utc - secondary.event_time_utc)
    divergence: Decimal | None = None
    if time_skew > max_source_time_skew_ns:
        flags.add(QualityFlag.STALE)
    else:
        divergence = _pct_difference(primary.last_price, secondary.last_price)
        if divergence > max_cross_source_divergence_pct:
            flags.add(QualityFlag.SOURCE_DIVERGENCE)
    live_vs_krx = _pct_difference(reference.previous_close, primary.last_price)
    if live_vs_krx > reference.max_live_move_pct:
        flags.add(QualityFlag.SOURCE_DIVERGENCE)

    is_live = primary.feed_mode is MarketDataFeedMode.LIVE
    blocking_flags = {QualityFlag.STALE, QualityFlag.SOURCE_DIVERGENCE}
    return LiveSnapshotQuality(
        instrument_id=primary.instrument_id,
        flags=frozenset(flags),
        is_live_primary=is_live,
        blocks_signal=(not is_live or bool(flags & blocking_flags)),
        live_vs_krx_move_pct=live_vs_krx,
        primary_vs_secondary_divergence_pct=divergence,
        source_time_skew_ns=time_skew,
    )


def _validate_scalar_inputs(
    fund_inputs: list[H1LiveFundInput],
    adv: Decimal,
    max_age_ns: int,
    max_skew_ns: int,
    divergence_pct: Decimal,
) -> None:
    if not fund_inputs:
        raise H1LiveInputError("fund_inputs는 비어 있을 수 없다")
    if adv <= 0:
        raise H1LiveInputError("underlying 20d ADV는 0보다 커야 한다")
    if max_age_ns < 0 or max_skew_ns < 0 or divergence_pct < 0:
        raise H1LiveInputError("freshness·대조 임계는 음수일 수 없다")


def _snapshot_map(batch: MarketSnapshotBatch) -> dict[str, MarketPriceSnapshot]:
    result = {item.instrument_id: item for item in batch.snapshots}
    if not result or len(result) != len(batch.snapshots):
        raise H1LiveInputError(f"{batch.provider_name} snapshot이 비었거나 중복됐다")
    if batch.received_at_utc != max(item.received_time_utc for item in batch.snapshots):
        raise H1LiveInputError(f"{batch.provider_name} batch received_at lineage가 다르다")
    return result


def _reference_map(
    references: list[KrxPreviousCloseReference],
) -> dict[str, KrxPreviousCloseReference]:
    result = {item.instrument_id: item for item in references}
    if not result or len(result) != len(references):
        raise H1LiveInputError("KRX previous close reference가 비었거나 중복됐다")
    return result


def _assert_reference_available(
    item: KrxPreviousCloseReference,
    trading_date: date,
    decision_time_utc: int,
) -> None:
    if item.basis_date >= trading_date:
        raise H1LiveInputError(f"{item.instrument_id} KRX 종가가 직전일 이전 값이 아니다")
    if item.received_at_utc > decision_time_utc:
        raise H1LiveInputError(f"{item.instrument_id} KRX 종가가 decision 후에 수신됐다")
    if item.previous_close <= 0 or item.max_live_move_pct < 0:
        raise H1LiveInputError(f"{item.instrument_id} KRX reference 가격·bound가 잘못됐다")
    if not item.input_record_id.strip():
        raise H1LiveInputError(f"{item.instrument_id} KRX lineage가 없다")


def _assert_fund_available(
    item: H1LiveFundInput,
    trading_date: date,
    decision_time_utc: int,
) -> None:
    if item.basis_date >= trading_date:
        raise H1LiveInputError(f"fund_id={item.fund_id}의 listed-notional이 직전일 이전이 아니다")
    if item.available_at_utc > decision_time_utc:
        raise H1LiveInputError(f"fund_id={item.fund_id}의 listed-notional이 decision 후에 가용해졌다")
    if item.listed_notional_proxy <= 0:
        raise H1LiveInputError(f"fund_id={item.fund_id}의 listed-notional은 0보다 커야 한다")
    if not item.input_record_ids or any(not value.strip() for value in item.input_record_ids):
        raise H1LiveInputError(f"fund_id={item.fund_id}의 lineage가 없다")


def _pct_difference(reference: Decimal, observed: Decimal) -> Decimal:
    if reference <= 0 or observed <= 0:
        raise H1LiveInputError("가격 대조 값은 0보다 커야 한다")
    return abs(observed - reference) / reference * Decimal("100")


class _LineageIds:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._values: list[str] = []

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(self._values)

    def add(self, value: str) -> None:
        if not value.strip():
            raise H1LiveInputError("빈 lineage record ID가 있다")
        if value not in self._seen:
            self._seen.add(value)
            self._values.append(value)
