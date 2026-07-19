"""Round 3 연속장 OFI feature의 계산·결측 계약."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from skhy_research.features.h1_close_pressure.continuous_ofi import (
    CREATION_TERM_STATUS,
    CausalDecimal,
    ContinuousOfiWindowInput,
    FeatureComputationStatus,
    FeatureDataOrigin,
    FeatureFailureReason,
    OrderBookEvent,
    ProgramEvent,
    ProgramValueSemantics,
    WindowQualityEvidence,
    calculate_best_quote_ofi_increment,
    compute_continuous_ofi_features,
)

_SECOND = 1_000_000_000
_END = 600 * _SECOND


def _levels(value: str) -> tuple[Decimal, ...]:
    return (Decimal(value),) * 10


def _book(
    second: int,
    *,
    bid: str,
    ask: str,
    bid_qty: str,
    ask_qty: str,
    record_id: str,
) -> OrderBookEvent:
    return OrderBookEvent(
        event_time_utc=second * _SECOND,
        bid_prices=_levels(bid),
        ask_prices=_levels(ask),
        bid_quantities=_levels(bid_qty),
        ask_quantities=_levels(ask_qty),
        input_record_id=record_id,
    )


def _quality() -> WindowQualityEvidence:
    return WindowQualityEvidence(
        clock_error_ms=Decimal("12"),
        quote_max_gap_seconds=Decimal("0.5"),
        trade_max_gap_seconds=Decimal("0.7"),
        price_quantity_units_confirmed=True,
        program_semantics_sealed=True,
    )


def _input(
    semantics: ProgramValueSemantics = ProgramValueSemantics.CUMULATIVE,
) -> ContinuousOfiWindowInput:
    program_values = ("100", "200", "300")
    if semantics is ProgramValueSemantics.INCREMENTAL:
        program_values = ("50", "75", "75")
    return ContinuousOfiWindowInput(
        window_start_utc=0,
        window_end_utc=_END,
        order_book_events=(
            _book(0, bid="100", ask="102", bid_qty="10", ask_qty="10", record_id="q0"),
            _book(
                300,
                bid="101",
                ask="102",
                bid_qty="20",
                ask_qty="5",
                record_id="q1",
            ),
            _book(
                600,
                bid="101",
                ask="103",
                bid_qty="15",
                ask_qty="20",
                record_id="q2",
            ),
        ),
        program_events=tuple(
            ProgramEvent(
                event_time_utc=second * _SECOND,
                net_buy_notional=Decimal(value),
                input_record_id=f"p{index}",
            )
            for index, (second, value) in enumerate(zip((0, 300, 600), program_values, strict=True))
        ),
        program_semantics=semantics,
        underlying_20d_adv_notional=CausalDecimal(Decimal("1000"), 0, "adv-t-minus-1"),
        tick_size=Decimal("1"),
        quality=_quality(),
        data_origin=FeatureDataOrigin.SANITIZED_FIXTURE,
    )


def test_cont_kukanov_stoikov_price_and_same_queue_branches() -> None:
    base = _book(0, bid="100", ask="102", bid_qty="10", ask_qty="10", record_id="base")
    bid_up = _book(1, bid="101", ask="102", bid_qty="20", ask_qty="10", record_id="up")
    bid_down = _book(1, bid="99", ask="102", bid_qty="20", ask_qty="10", record_id="down")
    same_queue = _book(
        1,
        bid="100",
        ask="102",
        bid_qty="13",
        ask_qty="7",
        record_id="same",
    )

    assert calculate_best_quote_ofi_increment(base, bid_up) == Decimal("20")
    assert calculate_best_quote_ofi_increment(base, bid_down) == Decimal("-10")
    # bid queue +3, ask queue 감소가 +3이므로 총 +6이다.
    assert calculate_best_quote_ofi_increment(base, same_queue) == Decimal("6")


def test_cumulative_program_and_time_weighted_features_follow_sealed_formula() -> None:
    result = compute_continuous_ofi_features(_input())

    assert result.status is FeatureComputationStatus.COMPUTABLE
    assert result.value is not None
    assert result.value.ofi_10m == Decimal("25")
    assert result.value.mean_best_depth_10m == Decimal("11.25")
    assert result.value.x_ofi == Decimal("25") / Decimal("11.25")
    assert result.value.x_depth == Decimal("0.3")
    assert abs(result.value.x_micro + Decimal("1") / Decimal("7")) < Decimal("1e-25")
    assert result.value.program_window_net_buy_notional == Decimal("200")
    assert result.value.x_program == Decimal("0.2")
    assert result.value.x_conflict == 0
    assert result.creation_term_status == CREATION_TERM_STATUS
    assert result.source_validation_eligible is False


def test_incremental_program_semantics_sums_only_the_sealed_window() -> None:
    result = compute_continuous_ofi_features(_input(ProgramValueSemantics.INCREMENTAL))

    assert result.value is not None
    assert result.value.program_window_net_buy_notional == Decimal("200")
    assert result.value.x_program == Decimal("0.2")


def test_post_cutoff_packets_do_not_change_feature_or_lineage() -> None:
    original = _input()
    expected = compute_continuous_ofi_features(original)
    post_cutoff_quote = _book(
        1200,
        bid="500",
        ask="501",
        bid_qty="999",
        ask_qty="1",
        record_id="forbidden-1520-quote",
    )
    post_cutoff_program = ProgramEvent(
        event_time_utc=1200 * _SECOND,
        net_buy_notional=Decimal("999999999"),
        input_record_id="forbidden-1520-program",
    )
    actual = compute_continuous_ofi_features(
        replace(
            original,
            order_book_events=(*original.order_book_events, post_cutoff_quote),
            program_events=(*original.program_events, post_cutoff_program),
        )
    )

    assert actual.value == expected.value
    assert "forbidden-1520-quote" not in actual.input_record_ids
    assert "forbidden-1520-program" not in actual.input_record_ids


def test_missing_depth_and_unsealed_program_are_null_not_zero() -> None:
    original = _input()
    incomplete = replace(
        original.order_book_events[1],
        bid_quantities=(None, *original.order_book_events[1].bid_quantities[1:]),
    )
    depth_result = compute_continuous_ofi_features(
        replace(
            original,
            order_book_events=(
                original.order_book_events[0],
                incomplete,
                original.order_book_events[2],
            ),
        )
    )
    semantics_result = compute_continuous_ofi_features(
        replace(
            original,
            program_semantics=ProgramValueSemantics.UNSEALED,
            quality=replace(original.quality, program_semantics_sealed=False),
        )
    )

    assert depth_result.value is None
    assert depth_result.status is FeatureComputationStatus.NOT_COMPUTABLE
    assert FeatureFailureReason.DEPTH_INCOMPLETE in depth_result.reasons
    assert semantics_result.value is None
    assert FeatureFailureReason.PROGRAM_SEMANTICS_UNSEALED in semantics_result.reasons


def test_packet_gap_and_late_adv_fail_closed() -> None:
    original = _input()
    result = compute_continuous_ofi_features(
        replace(
            original,
            quality=replace(original.quality, quote_max_gap_seconds=Decimal("2.01")),
            underlying_20d_adv_notional=CausalDecimal(Decimal("1000"), _END + 1, "late-adv"),
        )
    )

    assert result.value is None
    assert FeatureFailureReason.PACKET_GAP in result.reasons
    assert FeatureFailureReason.POST_CUTOFF_AVAILABLE in result.reasons
    assert "late-adv" not in result.input_record_ids
