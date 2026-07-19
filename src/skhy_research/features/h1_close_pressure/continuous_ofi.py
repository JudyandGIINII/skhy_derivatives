"""15:00~15:10 KRX 연속장 OFI/program proxy feature.

Round 3 봉인 설계의 계산식만 구현한다. 이 모듈은 raw 수집이나 주문 제출을 하지
않으며, 결측·품질 위반을 수치 0으로 바꾸지 않고 ``value=None``으로 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

H1_CONTINUOUS_OFI_MODEL_VERSION = "h1_kis_continuous_ofi_program_proxy_v1"
H1_CONTINUOUS_OFI_DATA_RESOLUTION = "intraday-continuous-15:00-15:10"
H1_CONTINUOUS_OFI_PROMOTION_SCOPE = "continuous-flow-proxy-research-only"
CREATION_TERM_STATUS = "EXCLUDED_UNAVAILABLE_SOURCE"
NANOSECONDS_PER_SECOND = Decimal("1000000000")
SEALED_WINDOW_SECONDS = Decimal("600")


class FeatureComputationStatus(StrEnum):
    COMPUTABLE = "COMPUTABLE"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"


class FeatureDataOrigin(StrEnum):
    LIVE_CAPTURE = "LIVE_CAPTURE"
    SANITIZED_FIXTURE = "SANITIZED_FIXTURE"


class ProgramValueSemantics(StrEnum):
    CUMULATIVE = "CUMULATIVE"
    INCREMENTAL = "INCREMENTAL"
    UNSEALED = "UNSEALED"


class FeatureFailureReason(StrEnum):
    SNAPSHOT_AFTER_151000 = "SNAPSHOT_AFTER_151000"
    POST_CUTOFF_AVAILABLE = "POST_CUTOFF_AVAILABLE"
    PROVIDER_EVENT_TIME_MISSING = "PROVIDER_EVENT_TIME_MISSING"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    CLOCK_SYNC_ERROR = "CLOCK_SYNC_ERROR"
    WEBSOCKET_DISCONNECT = "WEBSOCKET_DISCONNECT"
    PARSE_FAILURE = "PARSE_FAILURE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    PACKET_GAP = "PACKET_GAP"
    WINDOW_NOT_FULLY_COVERED = "WINDOW_NOT_FULLY_COVERED"
    DEPTH_INCOMPLETE = "DEPTH_INCOMPLETE"
    CROSSED_OR_LOCKED_BOOK = "CROSSED_OR_LOCKED_BOOK"
    PRICE_QUANTITY_UNIT_UNCONFIRMED = "PRICE_QUANTITY_UNIT_UNCONFIRMED"
    PROGRAM_MISSING = "PROGRAM_MISSING"
    PROGRAM_SEMANTICS_UNSEALED = "PROGRAM_SEMANTICS_UNSEALED"
    PROGRAM_SOURCE_NOT_KRX = "PROGRAM_SOURCE_NOT_KRX"
    PROGRAM_RESET_UNEXPLAINED = "PROGRAM_RESET_UNEXPLAINED"
    BEST_DEPTH_ZERO = "BEST_DEPTH_ZERO"
    TICK_SIZE_INVALID = "TICK_SIZE_INVALID"
    ADV_MISSING_OR_INVALID = "ADV_MISSING_OR_INVALID"
    STRUCTURAL_INPUT_MISSING = "STRUCTURAL_INPUT_MISSING"
    VI = "VI"
    HALTED = "HALTED"
    PRICE_LIMIT = "PRICE_LIMIT"
    MARKET_STATE_UNKNOWN = "MARKET_STATE_UNKNOWN"
    API_SCHEMA_DRIFT = "API_SCHEMA_DRIFT"
    FORBIDDEN_LINEAGE = "FORBIDDEN_LINEAGE"


@dataclass(frozen=True)
class CausalDecimal:
    value: Decimal | None
    available_at_utc: int | None
    input_record_id: str | None
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            if not self.missing_reason:
                raise ValueError("결측 causal 값에는 missing_reason이 필요하다")
            return
        if not self.value.is_finite():
            raise ValueError("causal 값은 유한해야 한다")
        if self.available_at_utc is None or self.available_at_utc < 0:
            raise ValueError("관측값에는 available_at_utc가 필요하다")
        if self.input_record_id is None or not self.input_record_id.strip():
            raise ValueError("관측값에는 input_record_id가 필요하다")
        if self.missing_reason is not None:
            raise ValueError("관측값에는 missing_reason을 둘 수 없다")


@dataclass(frozen=True)
class OrderBookEvent:
    event_time_utc: int | None
    bid_prices: tuple[Decimal | None, ...]
    ask_prices: tuple[Decimal | None, ...]
    bid_quantities: tuple[Decimal | None, ...]
    ask_quantities: tuple[Decimal | None, ...]
    input_record_id: str


@dataclass(frozen=True)
class ProgramEvent:
    event_time_utc: int | None
    net_buy_notional: Decimal | None
    input_record_id: str
    tr_id: str = "H0STPGM0"
    venue: str = "KRX"


@dataclass(frozen=True)
class WindowQualityEvidence:
    """일별 hard-filter 증거. ``None``은 통과가 아니라 미확정이다."""

    clock_error_ms: Decimal | None
    quote_max_gap_seconds: Decimal | None
    trade_max_gap_seconds: Decimal | None
    websocket_disconnects: int = 0
    parse_failures: int = 0
    out_of_order_events: int = 0
    trade_during_quote_gap: bool = False
    price_quantity_units_confirmed: bool = False
    program_semantics_sealed: bool = False
    unexplained_program_reset: bool = False
    vi: bool = False
    halted: bool = False
    price_limit: bool = False
    market_state_known: bool = True
    api_schema_drift: bool = False
    post_cutoff_feature_lineage: bool = False
    forbidden_outcome_lineage: bool = False


@dataclass(frozen=True)
class ContinuousOfiWindowInput:
    window_start_utc: int
    window_end_utc: int
    order_book_events: tuple[OrderBookEvent, ...]
    program_events: tuple[ProgramEvent, ...]
    program_semantics: ProgramValueSemantics
    underlying_20d_adv_notional: CausalDecimal
    tick_size: Decimal
    quality: WindowQualityEvidence
    data_origin: FeatureDataOrigin = FeatureDataOrigin.SANITIZED_FIXTURE
    structural_inputs_complete: bool = True
    structural_missing_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContinuousOfiFeatureVector:
    x_ofi: Decimal
    x_depth: Decimal
    x_micro: Decimal
    x_program: Decimal
    x_conflict: Decimal
    ofi_10m: Decimal
    mean_best_depth_10m: Decimal
    program_window_net_buy_notional: Decimal

    def as_mapping(self) -> dict[str, Decimal]:
        return {
            "x_ofi": self.x_ofi,
            "x_depth": self.x_depth,
            "x_micro": self.x_micro,
            "x_program": self.x_program,
            "x_conflict": self.x_conflict,
        }


@dataclass(frozen=True)
class ContinuousOfiFeatureResult:
    value: ContinuousOfiFeatureVector | None
    status: FeatureComputationStatus
    reasons: tuple[str, ...]
    input_record_ids: tuple[str, ...]
    creation_term_status: str = CREATION_TERM_STATUS
    model_version: str = H1_CONTINUOUS_OFI_MODEL_VERSION
    data_resolution: str = H1_CONTINUOUS_OFI_DATA_RESOLUTION
    promotion_scope: str = H1_CONTINUOUS_OFI_PROMOTION_SCOPE
    source_validation_eligible: bool = False


def calculate_best_quote_ofi_increment(
    previous: OrderBookEvent, current: OrderBookEvent
) -> Decimal:
    """Cont–Kukanov–Stoikov best-quote OFI event increment를 계산한다."""

    previous_values = _best_values(previous)
    current_values = _best_values(current)
    previous_bid, previous_ask, previous_bid_qty, previous_ask_qty = previous_values
    current_bid, current_ask, current_bid_qty, current_ask_qty = current_values
    increment = Decimal("0")
    if current_bid >= previous_bid:
        increment += current_bid_qty
    if current_bid <= previous_bid:
        increment -= previous_bid_qty
    if current_ask <= previous_ask:
        increment -= current_ask_qty
    if current_ask >= previous_ask:
        increment += previous_ask_qty
    return increment


def calculate_program_window_notional(
    events: tuple[ProgramEvent, ...],
    *,
    window_start_utc: int,
    window_end_utc: int,
    semantics: ProgramValueSemantics,
) -> Decimal:
    """봉인된 cumulative 차분 또는 incremental 합산 semantics를 적용한다."""

    eligible = tuple(
        event
        for event in events
        if event.event_time_utc is not None and event.event_time_utc <= window_end_utc
    )
    if semantics is ProgramValueSemantics.UNSEALED:
        raise ValueError(FeatureFailureReason.PROGRAM_SEMANTICS_UNSEALED)
    if any(event.tr_id != "H0STPGM0" or event.venue != "KRX" for event in eligible):
        raise ValueError(FeatureFailureReason.PROGRAM_SOURCE_NOT_KRX)
    if any(event.net_buy_notional is None for event in eligible):
        raise ValueError(FeatureFailureReason.PROGRAM_MISSING)
    if semantics is ProgramValueSemantics.CUMULATIVE:
        starts = [
            event
            for event in eligible
            if event.event_time_utc is not None and event.event_time_utc <= window_start_utc
        ]
        ends = [event for event in eligible if event.event_time_utc is not None]
        if not starts or not ends:
            raise ValueError(FeatureFailureReason.PROGRAM_MISSING)
        start_value = starts[-1].net_buy_notional
        end_value = ends[-1].net_buy_notional
        assert start_value is not None
        assert end_value is not None
        return end_value - start_value
    values = (
        event.net_buy_notional
        for event in eligible
        if event.event_time_utc is not None
        and window_start_utc <= event.event_time_utc <= window_end_utc
    )
    return sum((value for value in values if value is not None), Decimal("0"))


def compute_continuous_ofi_features(
    inputs: ContinuousOfiWindowInput,
) -> ContinuousOfiFeatureResult:
    """봉인된 10분 feature를 계산하거나 명시적인 NOT_COMPUTABLE을 반환한다."""

    reasons = _hard_filter_reasons(inputs)
    lineage = _eligible_lineage(inputs)
    if reasons:
        return _missing_result(reasons, lineage)

    events = tuple(
        event
        for event in inputs.order_book_events
        if event.event_time_utc is not None and event.event_time_utc <= inputs.window_end_utc
    )
    baseline_index = max(
        index
        for index, event in enumerate(events)
        if event.event_time_utc is not None and event.event_time_utc <= inputs.window_start_utc
    )
    active_events = events[baseline_index:]
    ofi = Decimal("0")
    for previous, current in zip(active_events, active_events[1:], strict=False):
        assert current.event_time_utc is not None
        if current.event_time_utc > inputs.window_start_utc:
            ofi += calculate_best_quote_ofi_increment(previous, current)

    weighted_best_depth = Decimal("0")
    weighted_depth_imbalance = Decimal("0")
    for index, event in enumerate(active_events):
        assert event.event_time_utc is not None
        interval_start = max(event.event_time_utc, inputs.window_start_utc)
        next_time = inputs.window_end_utc
        if index + 1 < len(active_events):
            candidate = active_events[index + 1].event_time_utc
            assert candidate is not None
            next_time = min(candidate, inputs.window_end_utc)
        duration = _seconds(next_time - interval_start)
        if duration <= 0:
            continue
        weighted_best_depth += _best_depth(event) * duration
        weighted_depth_imbalance += _depth_imbalance(event) * duration

    mean_best_depth = weighted_best_depth / SEALED_WINDOW_SECONDS
    if mean_best_depth <= 0:
        return _missing_result((FeatureFailureReason.BEST_DEPTH_ZERO.value,), lineage)
    x_ofi = ofi / mean_best_depth
    x_depth = weighted_depth_imbalance / SEALED_WINDOW_SECONDS
    last_quote = active_events[-1]
    bid, ask, bid_qty, ask_qty = _best_values(last_quote)
    denominator = bid_qty + ask_qty
    if denominator <= 0:
        return _missing_result((FeatureFailureReason.BEST_DEPTH_ZERO.value,), lineage)
    microprice = (ask * bid_qty + bid * ask_qty) / denominator
    midprice = (bid + ask) / Decimal("2")
    x_micro = (microprice - midprice) / inputs.tick_size
    try:
        program_notional = calculate_program_window_notional(
            inputs.program_events,
            window_start_utc=inputs.window_start_utc,
            window_end_utc=inputs.window_end_utc,
            semantics=inputs.program_semantics,
        )
    except ValueError as exc:
        return _missing_result((str(exc),), lineage)
    adv = inputs.underlying_20d_adv_notional.value
    assert adv is not None
    x_program = program_notional / adv
    conflict = Decimal(int(x_ofi != 0 and x_program != 0 and (x_ofi > 0) != (x_program > 0)))
    vector = ContinuousOfiFeatureVector(
        x_ofi=x_ofi,
        x_depth=x_depth,
        x_micro=x_micro,
        x_program=x_program,
        x_conflict=conflict,
        ofi_10m=ofi,
        mean_best_depth_10m=mean_best_depth,
        program_window_net_buy_notional=program_notional,
    )
    return ContinuousOfiFeatureResult(
        value=vector,
        status=FeatureComputationStatus.COMPUTABLE,
        reasons=(),
        input_record_ids=lineage,
        source_validation_eligible=inputs.data_origin is FeatureDataOrigin.LIVE_CAPTURE,
    )


def _hard_filter_reasons(inputs: ContinuousOfiWindowInput) -> tuple[str, ...]:
    reasons: list[str] = []
    duration = _seconds(inputs.window_end_utc - inputs.window_start_utc)
    if duration != SEALED_WINDOW_SECONDS:
        reasons.append(FeatureFailureReason.WINDOW_NOT_FULLY_COVERED.value)
    if inputs.tick_size <= 0 or not inputs.tick_size.is_finite():
        reasons.append(FeatureFailureReason.TICK_SIZE_INVALID.value)
    adv = inputs.underlying_20d_adv_notional
    if adv.value is None or adv.value <= 0:
        reasons.append(FeatureFailureReason.ADV_MISSING_OR_INVALID.value)
    elif adv.available_at_utc is None or adv.available_at_utc > inputs.window_end_utc:
        reasons.append(FeatureFailureReason.POST_CUTOFF_AVAILABLE.value)
    if not inputs.structural_inputs_complete:
        reasons.append(FeatureFailureReason.STRUCTURAL_INPUT_MISSING.value)

    quality = inputs.quality
    if quality.clock_error_ms is None or abs(quality.clock_error_ms) > Decimal("50"):
        reasons.append(FeatureFailureReason.CLOCK_SYNC_ERROR.value)
    if quality.websocket_disconnects:
        reasons.append(FeatureFailureReason.WEBSOCKET_DISCONNECT.value)
    if quality.parse_failures:
        reasons.append(FeatureFailureReason.PARSE_FAILURE.value)
    if quality.out_of_order_events:
        reasons.append(FeatureFailureReason.OUT_OF_ORDER.value)
    if (
        quality.quote_max_gap_seconds is None
        or quality.trade_max_gap_seconds is None
        or quality.quote_max_gap_seconds > Decimal("2")
        or quality.trade_max_gap_seconds > Decimal("2")
        or quality.trade_during_quote_gap
    ):
        reasons.append(FeatureFailureReason.PACKET_GAP.value)
    if not quality.price_quantity_units_confirmed:
        reasons.append(FeatureFailureReason.PRICE_QUANTITY_UNIT_UNCONFIRMED.value)
    if not quality.program_semantics_sealed:
        reasons.append(FeatureFailureReason.PROGRAM_SEMANTICS_UNSEALED.value)
    if quality.unexplained_program_reset:
        reasons.append(FeatureFailureReason.PROGRAM_RESET_UNEXPLAINED.value)
    if quality.vi:
        reasons.append(FeatureFailureReason.VI.value)
    if quality.halted:
        reasons.append(FeatureFailureReason.HALTED.value)
    if quality.price_limit:
        reasons.append(FeatureFailureReason.PRICE_LIMIT.value)
    if not quality.market_state_known:
        reasons.append(FeatureFailureReason.MARKET_STATE_UNKNOWN.value)
    if quality.api_schema_drift:
        reasons.append(FeatureFailureReason.API_SCHEMA_DRIFT.value)
    if quality.post_cutoff_feature_lineage:
        reasons.append(FeatureFailureReason.POST_CUTOFF_AVAILABLE.value)
    if quality.forbidden_outcome_lineage:
        reasons.append(FeatureFailureReason.FORBIDDEN_LINEAGE.value)

    reasons.extend(_event_reasons(inputs))
    return tuple(dict.fromkeys(reasons))


def _event_reasons(inputs: ContinuousOfiWindowInput) -> tuple[str, ...]:
    reasons: list[str] = []
    quote_times = [event.event_time_utc for event in inputs.order_book_events]
    program_times = [event.event_time_utc for event in inputs.program_events]
    if any(value is None for value in (*quote_times, *program_times)):
        reasons.append(FeatureFailureReason.PROVIDER_EVENT_TIME_MISSING.value)
        return tuple(reasons)
    concrete_quote_times = [value for value in quote_times if value is not None]
    concrete_program_times = [value for value in program_times if value is not None]
    if not _strictly_increasing(concrete_quote_times) or not _strictly_increasing(
        concrete_program_times
    ):
        reasons.append(FeatureFailureReason.OUT_OF_ORDER.value)
    eligible_quotes = [
        event
        for event in inputs.order_book_events
        if event.event_time_utc is not None and event.event_time_utc <= inputs.window_end_utc
    ]
    baseline = [
        event
        for event in eligible_quotes
        if event.event_time_utc is not None and event.event_time_utc <= inputs.window_start_utc
    ]
    if not baseline:
        if any(
            event.event_time_utc is not None and event.event_time_utc > inputs.window_end_utc
            for event in inputs.order_book_events
        ):
            reasons.append(FeatureFailureReason.SNAPSHOT_AFTER_151000.value)
        reasons.append(FeatureFailureReason.WINDOW_NOT_FULLY_COVERED.value)
    if not eligible_quotes:
        reasons.append(FeatureFailureReason.WINDOW_NOT_FULLY_COVERED.value)
    else:
        last_time = eligible_quotes[-1].event_time_utc
        assert last_time is not None
        if _seconds(inputs.window_end_utc - last_time) > Decimal("2"):
            reasons.append(FeatureFailureReason.SNAPSHOT_STALE.value)
    for event in eligible_quotes:
        if not _has_complete_depth(event):
            reasons.append(FeatureFailureReason.DEPTH_INCOMPLETE.value)
            break
        bid, ask, _, _ = _best_values(event)
        if bid >= ask:
            reasons.append(FeatureFailureReason.CROSSED_OR_LOCKED_BOOK.value)
            break
        total_depth = sum(
            (
                value
                for value in (*event.bid_quantities, *event.ask_quantities)
                if value is not None
            ),
            Decimal("0"),
        )
        if total_depth <= 0:
            reasons.append(FeatureFailureReason.BEST_DEPTH_ZERO.value)
            break

    eligible_program = [
        event
        for event in inputs.program_events
        if event.event_time_utc is not None and event.event_time_utc <= inputs.window_end_utc
    ]
    if not eligible_program:
        reasons.append(FeatureFailureReason.PROGRAM_MISSING.value)
    else:
        if any(event.tr_id != "H0STPGM0" or event.venue != "KRX" for event in eligible_program):
            reasons.append(FeatureFailureReason.PROGRAM_SOURCE_NOT_KRX.value)
        if any(event.net_buy_notional is None for event in eligible_program):
            reasons.append(FeatureFailureReason.PROGRAM_MISSING.value)
        last_program_time = eligible_program[-1].event_time_utc
        assert last_program_time is not None
        if _seconds(inputs.window_end_utc - last_program_time) > Decimal("2"):
            reasons.append(FeatureFailureReason.SNAPSHOT_STALE.value)
        if inputs.program_semantics is ProgramValueSemantics.CUMULATIVE and not any(
            event.event_time_utc is not None and event.event_time_utc <= inputs.window_start_utc
            for event in eligible_program
        ):
            reasons.append(FeatureFailureReason.PROGRAM_MISSING.value)
    if inputs.program_semantics is ProgramValueSemantics.UNSEALED:
        reasons.append(FeatureFailureReason.PROGRAM_SEMANTICS_UNSEALED.value)
    return tuple(reasons)


def _eligible_lineage(inputs: ContinuousOfiWindowInput) -> tuple[str, ...]:
    values: list[str] = []
    for event in (*inputs.order_book_events, *inputs.program_events):
        if (
            event.event_time_utc is not None
            and event.event_time_utc <= inputs.window_end_utc
            and event.input_record_id
            and event.input_record_id not in values
        ):
            values.append(event.input_record_id)
    adv_record = inputs.underlying_20d_adv_notional.input_record_id
    adv_available_at = inputs.underlying_20d_adv_notional.available_at_utc
    if (
        adv_record
        and adv_available_at is not None
        and adv_available_at <= inputs.window_end_utc
        and adv_record not in values
    ):
        values.append(adv_record)
    return tuple(values)


def _missing_result(
    reasons: tuple[str, ...], lineage: tuple[str, ...]
) -> ContinuousOfiFeatureResult:
    return ContinuousOfiFeatureResult(
        value=None,
        status=FeatureComputationStatus.NOT_COMPUTABLE,
        reasons=tuple(dict.fromkeys(reasons)),
        input_record_ids=lineage,
        source_validation_eligible=False,
    )


def _best_values(event: OrderBookEvent) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if not _has_complete_depth(event):
        raise ValueError(FeatureFailureReason.DEPTH_INCOMPLETE)
    bid = event.bid_prices[0]
    ask = event.ask_prices[0]
    bid_qty = event.bid_quantities[0]
    ask_qty = event.ask_quantities[0]
    assert bid is not None
    assert ask is not None
    assert bid_qty is not None
    assert ask_qty is not None
    return bid, ask, bid_qty, ask_qty


def _has_complete_depth(event: OrderBookEvent) -> bool:
    collections = (
        event.bid_prices,
        event.ask_prices,
        event.bid_quantities,
        event.ask_quantities,
    )
    if any(len(values) != 10 for values in collections):
        return False
    for values in collections:
        for value in values:
            if value is None or not value.is_finite() or value < 0:
                return False
    return all(value is not None and value > 0 for value in (*event.bid_prices, *event.ask_prices))


def _best_depth(event: OrderBookEvent) -> Decimal:
    _, _, bid_qty, ask_qty = _best_values(event)
    return (bid_qty + ask_qty) / Decimal("2")


def _depth_imbalance(event: OrderBookEvent) -> Decimal:
    if not _has_complete_depth(event):
        raise ValueError(FeatureFailureReason.DEPTH_INCOMPLETE)
    bid_total = sum((value for value in event.bid_quantities if value is not None), Decimal("0"))
    ask_total = sum((value for value in event.ask_quantities if value is not None), Decimal("0"))
    denominator = bid_total + ask_total
    if denominator <= 0:
        raise ValueError(FeatureFailureReason.BEST_DEPTH_ZERO)
    return (bid_total - ask_total) / denominator


def _seconds(nanoseconds: int) -> Decimal:
    return Decimal(nanoseconds) / NANOSECONDS_PER_SECOND


def _strictly_increasing(values: list[int]) -> bool:
    return all(current > previous for previous, current in zip(values, values[1:], strict=False))
