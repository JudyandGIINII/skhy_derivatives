"""G9 train 잔차화·t-1·공매도 t-2 계약."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from datetime import time as wall_time
from decimal import Decimal
from zoneinfo import ZoneInfo

from skhy_research.features.g9_idiosyncratic_flow import (
    G9ResidualizationConfig,
    InvestorFlowScope,
    InvestorNetBuyObservation,
    ShortSaleObservation,
    build_g9_features,
    fit_g9_residualization,
)

_SEOUL = ZoneInfo("Asia/Seoul")
_NS = 1_000_000_000


def _nanos(day: date, hour: int, minute: int = 0) -> int:
    return int(
        datetime.combine(day, wall_time(hour, minute), tzinfo=_SEOUL).timestamp() * _NS
    )


def _inputs(count: int = 50) -> tuple[list[InvestorNetBuyObservation], list[ShortSaleObservation]]:
    start = date(2024, 1, 2)
    flow: list[InvestorNetBuyObservation] = []
    short: list[ShortSaleObservation] = []
    for index in range(count):
        day = start + timedelta(days=index)
        samsung = Decimal((index % 7) - 3) * 100
        semiconductor = Decimal((index % 11) - 5) * 80 + index
        market = Decimal((index % 13) - 6) * 60 + index * 2
        residual = Decimal((index % 5) - 2) * 10
        values = {
            InvestorFlowScope.SKHY_000660: (
                Decimal(2) * samsung
                + Decimal(3) * semiconductor
                + Decimal(4) * market
                + residual
            ),
            InvestorFlowScope.SAMSUNG_005930: samsung,
            InvestorFlowScope.SEMICONDUCTOR: semiconductor,
            InvestorFlowScope.MARKET: market,
        }
        for scope, value in values.items():
            flow.append(
                InvestorNetBuyObservation(
                    trading_date=day,
                    scope=scope,
                    investor="외국인 합계",
                    net_buy_notional=value,
                    event_time_utc=_nanos(day, 15, 30),
                    available_at_utc=_nanos(day, 18, 10),
                    source=f"manual:{scope.value}",
                    input_record_id=f"flow:{scope.value}:{day}",
                )
            )
        short.append(
            ShortSaleObservation(
                trading_date=day,
                symbol="000660",
                short_volume=Decimal(1000 + index),
                short_balance=Decimal(5000 - index * 10),
                event_time_utc=_nanos(day, 15, 30),
                volume_available_at_utc=_nanos(day, 18, 10),
                source="manual:MDCSTAT300",
                input_record_id=f"short:{day}",
            )
        )
    return flow, short


def test_coefficients_use_only_train_and_idio_feature_is_lag_one() -> None:
    flow, short = _inputs()
    config = G9ResidualizationConfig(
        investor="외국인 합계",
        train_start=date(2024, 1, 2),
        train_end=date(2024, 2, 10),
        minimum_train_observations=30,
    )
    fit = fit_g9_residualization(flow, config)
    build = build_g9_features(flow, fit, short_sale_observations=short)

    assert fit.observation_count == 40
    assert abs(fit.b1_samsung - Decimal(2)) < Decimal("0.1")
    assert abs(fit.b2_semiconductor - Decimal(3)) < Decimal("0.1")
    assert abs(fit.b3_market - Decimal(4)) < Decimal("0.1")
    feature = build.features[2]
    assert feature.trading_date == date(2024, 1, 5)
    assert feature.source_flow_date == date(2024, 1, 4)
    assert feature.short_volume_lag1 == 1002
    assert feature.short_balance_lag2 == 4990
    assert feature.short_balance_change_lag2 == -10
    assert "flow:000660:2024-01-04" in feature.input_record_ids
    assert "flow:000660:2024-01-05" not in feature.input_record_ids


def test_post_train_values_cannot_change_coefficients() -> None:
    flow, _ = _inputs()
    config = G9ResidualizationConfig(
        investor="외국인 합계",
        train_start=date(2024, 1, 2),
        train_end=date(2024, 2, 10),
        minimum_train_observations=30,
    )
    original = fit_g9_residualization(flow, config)
    changed = [
        replace(item, net_buy_notional=item.net_buy_notional * Decimal(1_000_000))
        if item.trading_date > config.train_end
        else item
        for item in flow
    ]

    assert fit_g9_residualization(changed, config) == original


def test_t_minus_one_post_cutoff_is_dropped_not_zero_filled() -> None:
    flow, short = _inputs()
    config = G9ResidualizationConfig(
        investor="외국인 합계",
        train_start=date(2024, 1, 2),
        train_end=date(2024, 2, 10),
        minimum_train_observations=30,
    )
    fit = fit_g9_residualization(flow, config)
    source_day = date(2024, 2, 11)
    late = [
        replace(item, available_at_utc=_nanos(date(2024, 2, 12), 10))
        if item.trading_date == source_day and item.scope is InvestorFlowScope.MARKET
        else item
        for item in flow
    ]
    build = build_g9_features(late, fit, short_sale_observations=short)

    assert build.missing_reason_counts["INVESTOR_FLOW_LAG1_POST_CUTOFF"] == 1
    assert date(2024, 2, 12) not in {item.trading_date for item in build.features}


def test_missing_short_data_stays_explicit_auxiliary_missing() -> None:
    flow, _ = _inputs()
    config = G9ResidualizationConfig(
        investor="외국인 합계",
        train_start=date(2024, 1, 2),
        train_end=date(2024, 2, 10),
        minimum_train_observations=30,
    )
    fit = fit_g9_residualization(flow, config)
    build = build_g9_features(flow, fit)

    assert build.features[5].short_volume_lag1 is None
    assert build.features[5].short_balance_lag2 is None
    assert "SHORT_VOLUME_T_MINUS_1_UNAVAILABLE" in build.features[5].missing_auxiliary_reasons
