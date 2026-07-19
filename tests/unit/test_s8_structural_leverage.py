"""S8 구조적 레버리지 피처·결측·120일 HOLD 계약."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from datetime import time as wall_time
from decimal import Decimal
from zoneinfo import ZoneInfo

from skhy_research.features.s8_structural_leverage import (
    S8DailyObservation,
    S8DataOrigin,
    S8Status,
    S8TimedValue,
    build_s8_structural_leverage_skeleton,
)

_SEOUL = ZoneInfo("Asia/Seoul")
_NS = 1_000_000_000


def _nanos(day: date, hour: int, minute: int = 0) -> int:
    return int(
        datetime.combine(day, wall_time(hour, minute), tzinfo=_SEOUL).timestamp() * _NS
    )


def _value(value: Decimal, day: date, name: str, unit: str) -> S8TimedValue:
    event = _nanos(day, 15, 30)
    return S8TimedValue(
        value=value,
        event_time_utc=event,
        available_at_utc=_nanos(day, 18, 10),
        source=f"KRX:{name}",
        unit=unit,
        input_record_id=f"{name}:{day}",
    )


def _observations(
    count: int = 35,
    *,
    origin: S8DataOrigin = S8DataOrigin.KRX_ACTUAL,
) -> list[S8DailyObservation]:
    start = date(2026, 5, 27)
    result = []
    for index in range(count):
        day = start + timedelta(days=index)
        result.append(
            S8DailyObservation(
                trading_date=day,
                product_symbol="LEVERAGED_000660",
                nav_per_share=_value(Decimal(100 + index), day, "nav", "KRW_PER_SHARE"),
                listed_shares=_value(
                    Decimal(1_000_000 + index * 10), day, "shares", "SHARES"
                ),
                market_close=_value(Decimal(101 + index), day, "close", "KRW"),
                product_return=_value(Decimal("0.02"), day, "product_return", "RETURN"),
                underlying_return=_value(
                    Decimal("0.009"), day, "underlying_return", "RETURN"
                ),
                leverage_multiple=Decimal(2),
                data_origin=origin,
            )
        )
    return result


def test_five_features_are_separate_lagged_and_lineaged() -> None:
    result = build_s8_structural_leverage_skeleton(_observations())

    assert result.status is S8Status.HOLD_SAMPLE_INSUFFICIENT
    assert result.observation_count == 35
    assert result.feature_count == 34
    row = result.features[1]
    assert row.nav.value == 101
    assert row.listed_shares.value == 1_000_010
    assert row.delta_shares.value == 10
    assert row.nav_premium.value == Decimal(102) / Decimal(101) - Decimal(1)
    assert row.tracking_error.value == Decimal("0.002")
    assert row.delta_shares.input_record_ids == (
        "shares:2026-05-28",
        "shares:2026-05-27",
    )


def test_35_actual_days_is_explicit_120_day_hold_without_tuning_or_sealed_test() -> None:
    result = build_s8_structural_leverage_skeleton(_observations())

    assert result.reasons == (
        "PRD_10_2_H1_MINIMUM_120_TRADING_DAYS_NOT_MET",
        "ACTUAL_TRADING_DAYS:35",
    )
    assert result.hyperparameter_search_enabled is False
    assert result.sealed_test_enabled is False
    assert result.proxy_or_synthetic_performance_allowed is False
    assert result.order_submission_enabled is False


def test_missing_nav_is_not_filled_and_has_no_fake_lineage() -> None:
    observations = _observations(4)
    missing_nav = S8TimedValue(
        value=None,
        event_time_utc=None,
        available_at_utc=None,
        source="KRX:nav",
        unit="KRW_PER_SHARE",
        input_record_id=None,
        missing_reason="NAV_NOT_PUBLISHED",
    )
    observations[1] = replace(observations[1], nav_per_share=missing_nav)

    result = build_s8_structural_leverage_skeleton(observations)
    row = result.features[1]

    assert row.nav.value is None
    assert row.nav.missing_reason == "NAV:NAV_NOT_PUBLISHED"
    assert row.nav.input_record_ids == ()
    assert row.nav_premium.value is None


def test_post_cutoff_value_is_missing_not_lookahead() -> None:
    observations = _observations(3)
    late = replace(
        observations[0].nav_per_share,
        available_at_utc=_nanos(observations[1].trading_date, 10),
    )
    observations[0] = replace(observations[0], nav_per_share=late)

    result = build_s8_structural_leverage_skeleton(observations)

    assert result.features[0].nav.missing_reason == "NAV_T_MINUS_1_POST_CUTOFF"
    assert result.features[0].nav.value is None


def test_fixture_never_becomes_skhy_performance_evidence() -> None:
    result = build_s8_structural_leverage_skeleton(
        _observations(130, origin=S8DataOrigin.SANITIZED_FIXTURE)
    )

    assert result.status is S8Status.FIXTURE_ONLY
    assert result.reasons == ("SANITIZED_FIXTURE_NOT_PERFORMANCE_EVIDENCE",)
