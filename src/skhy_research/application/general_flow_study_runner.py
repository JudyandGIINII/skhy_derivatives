"""KRX 수동 CSV→G9→D3 일반수급 사전반증 실행 조립."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as wall_time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from skhy_research.adapters.providers.krx.research_data import (
    KrxResearchDatasetAvailability,
    research_dataset_availability,
)
from skhy_research.application.krx_general_flow_backfill import (
    AppendOnlyBackfillArtifact,
    load_krx_investor_net_buy_csv,
    load_krx_mdcstat300_short_sale_csv,
    persist_investor_flow_append_only,
    persist_short_sale_append_only,
)
from skhy_research.features.g9_idiosyncratic_flow import (
    G9ResidualizationConfig,
    G9ResidualizationFit,
    InvestorFlowScope,
    build_g9_features,
    fit_g9_residualization,
)
from skhy_research.prefalsification.general_flow_study import (
    FlowReturnObservation,
    GeneralFlowStudyConfig,
    GeneralFlowStudyResult,
    build_general_flow_hold,
    build_general_flow_rows,
    build_negative_control_specs,
    run_general_flow_study,
)

_SEOUL = ZoneInfo("Asia/Seoul")
_NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class GeneralFlowExecutionResult:
    study: GeneralFlowStudyResult
    backfill_artifacts: tuple[AppendOnlyBackfillArtifact, ...]
    open_api_availability: tuple[KrxResearchDatasetAvailability, ...]
    residualization_fit: G9ResidualizationFit | None


def execute_general_flow_study(
    *,
    data_root: Path,
    price_snapshot_path: Path | None,
    investor_csv_paths: Mapping[InvestorFlowScope, Path],
    short_sale_csv_path: Path | None,
    investor: str,
    train_end: date | None,
    actual_product_listing_date: date,
    fake_listing_dates: Sequence[date],
    config: GeneralFlowStudyConfig | None = None,
) -> GeneralFlowExecutionResult:
    """제공된 수동 원본만 적재하고 부족하면 즉시 HOLD를 반환한다."""

    artifacts: list[AppendOnlyBackfillArtifact] = []
    flow_loads = {}
    for scope, path in investor_csv_paths.items():
        loaded = load_krx_investor_net_buy_csv(path, scope=scope)
        flow_loads[scope] = loaded
        artifacts.append(persist_investor_flow_append_only(loaded, data_root))
    short_load = None
    if short_sale_csv_path is not None:
        short_load = load_krx_mdcstat300_short_sale_csv(short_sale_csv_path)
        artifacts.append(persist_short_sale_append_only(short_load, data_root))

    missing = [
        f"krx_investor_net_buy:{scope.value}"
        for scope in InvestorFlowScope
        if scope not in flow_loads
    ]
    if short_load is None:
        missing.append("krx_short_selling_comprehensive:MDCSTAT300:000660")
    if price_snapshot_path is None or not price_snapshot_path.exists():
        missing.append("krx_daily_price_outcomes:000660,005930")
    if train_end is None:
        missing.append("g9_residualization_train_end")
    artifact_ids = tuple(
        f"append-only-artifact:{item.dataset}:{item.content_sha256}" for item in artifacts
    )
    observed_days = min(
        (item.trading_day_count for item in flow_loads.values()), default=0
    )
    if missing:
        return GeneralFlowExecutionResult(
            study=build_general_flow_hold(
                missing_datasets=missing,
                scheduled_trading_days=observed_days,
                input_record_ids=artifact_ids,
            ),
            backfill_artifacts=tuple(artifacts),
            open_api_availability=research_dataset_availability(),
            residualization_fit=None,
        )

    all_flow = tuple(
        observation
        for scope in InvestorFlowScope
        for observation in flow_loads[scope].observations
    )
    investor_dates = [
        item.trading_date for item in all_flow if item.investor == investor
    ]
    if not investor_dates:
        return GeneralFlowExecutionResult(
            study=build_general_flow_hold(
                missing_datasets=(f"investor_column:{investor}",),
                scheduled_trading_days=observed_days,
                input_record_ids=artifact_ids,
            ),
            backfill_artifacts=tuple(artifacts),
            open_api_availability=research_dataset_availability(),
            residualization_fit=None,
        )
    assert train_end is not None
    try:
        fit = fit_g9_residualization(
            all_flow,
            G9ResidualizationConfig(
                investor=investor,
                train_start=min(investor_dates),
                train_end=train_end,
            ),
        )
    except ValueError as exc:
        return GeneralFlowExecutionResult(
            study=build_general_flow_hold(
                missing_datasets=(f"g9_aligned_train_data:{exc}",),
                scheduled_trading_days=observed_days,
                input_record_ids=artifact_ids,
            ),
            backfill_artifacts=tuple(artifacts),
            open_api_availability=research_dataset_availability(),
            residualization_fit=None,
        )
    assert short_load is not None
    build = build_g9_features(
        all_flow,
        fit,
        short_sale_observations=short_load.observations,
    )
    assert price_snapshot_path is not None
    outcomes = load_krx_price_outcomes(price_snapshot_path)
    primary_rows = build_general_flow_rows(build.features, outcomes, symbol="000660")
    peer_rows = build_general_flow_rows(build.features, outcomes, symbol="005930")
    specs = build_negative_control_specs(
        primary_rows,
        peer_rows={"005930": peer_rows},
        actual_product_listing_date=actual_product_listing_date,
        fake_listing_dates=fake_listing_dates,
    )
    study = run_general_flow_study(
        primary_rows,
        scheduled_trading_days=build.common_flow_trading_days,
        negative_control_specs=specs,
        config=config,
    )
    return GeneralFlowExecutionResult(
        study=study,
        backfill_artifacts=tuple(artifacts),
        open_api_availability=research_dataset_availability(),
        residualization_fit=fit,
    )


def load_krx_price_outcomes(path: Path) -> tuple[FlowReturnObservation, ...]:
    """기존 weak KRX 스냅샷에서 000660·005930 시가→종가를 적재한다."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_observations = payload.get("observations")
    if not isinstance(raw_observations, list):
        raise ValueError("KRX price snapshot에 observations 배열이 없다")
    outcomes: list[FlowReturnObservation] = []
    for raw in raw_observations:
        if not isinstance(raw, dict):
            continue
        try:
            trading_date = date.fromisoformat(str(raw["trading_date"]))
        except (KeyError, ValueError):
            continue
        stock_record_id = str(raw.get("stock_record_id", "")).strip()
        target = raw.get("000660")
        if isinstance(target, dict):
            target_open = _decimal(target.get("open"))
            target_close = _decimal(target.get("close"))
            if (
                target_open is not None
                and target_close is not None
                and target_open > 0
                and target_close > 0
                and stock_record_id
            ):
                outcomes.append(
                    _outcome(
                        trading_date,
                        "000660",
                        Decimal(str(math.log(float(target_close / target_open)))),
                        stock_record_id,
                    )
                )
        samsung_return = _decimal(raw.get("005930_open_to_close_return"))
        if samsung_return is not None and stock_record_id:
            outcomes.append(
                _outcome(
                    trading_date,
                    "005930",
                    samsung_return,
                    stock_record_id,
                )
            )
    if not outcomes:
        raise ValueError(f"KRX price snapshot에 유효한 결과값이 없다: {path}")
    outcomes.sort(key=lambda item: (item.trading_date, item.symbol))
    return tuple(outcomes)


def _outcome(
    trading_date: date,
    symbol: str,
    value: Decimal,
    record_id: str,
) -> FlowReturnObservation:
    return FlowReturnObservation(
        trading_date=trading_date,
        symbol=symbol,
        open_to_close_return=value,
        market_open_utc=_seoul_nanos(trading_date, wall_time(9, 0)),
        official_close_utc=_seoul_nanos(trading_date, wall_time(15, 30)),
        source=f"KRX_OPEN_API:stk_bydd_trd:{symbol}:OPEN_TO_CLOSE",
        input_record_id=record_id,
    )


def _decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw).replace(",", ""))
    except Exception:
        return None
    return value if value.is_finite() else None


def _seoul_nanos(day: date, clock_time: wall_time) -> int:
    return int(datetime.combine(day, clock_time, tzinfo=_SEOUL).timestamp() * _NS_PER_SECOND)
