"""수집·백필·검증·백테스트·페이퍼 실행·리포트용 CLI 엔트리포인트.

모든 서브커맨드는 콜백에서 먼저 부팅 게이트(bootstrap)를 통과해야 한다.
Phase 1의 KRX 실백필은 gate journal을 먼저 확인하고 조회 전용 provider만 조립한다.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from skhy_research.application.boot import bootstrap
from skhy_research.application.config import Settings, load_settings

app = typer.Typer(help="SK하이닉스 구조적 수급·교차시장 상대가치 연구 시스템 CLI")
gate_app = typer.Typer(help="gate 결정 관리 (PostgreSQL journal)")
app.add_typer(gate_app, name="gate")


@app.callback()
def main(
    ctx: typer.Context,
    env: str = typer.Option("local", "--env", envvar="SKHY_ENV", help="SKHY_ENV 값"),
) -> None:
    settings = load_settings(env)
    bootstrap(settings)
    ctx.obj = settings


@app.command("config-check")
def config_check(ctx: typer.Context) -> None:
    """설정이 정상 로드되는지 확인하고 config_hash를 출력한다."""
    settings: Settings = ctx.obj
    typer.echo(f"env={settings.env_name} broker_mode={settings.broker_mode}")
    typer.echo(f"config_hash={settings.config_hash}")


@gate_app.command("seed")
def gate_seed(ctx: typer.Context) -> None:
    """확정된 gate 결정(G-02/G-04/G-06)을 PostgreSQL journal에 멱등 저장한다.

    Markdown의 CONFIRMED 표기만으로는 런타임 gate가 열리지 않는다. 이 명령이
    기계용 진실의 출처인 journal에 확정 결정을 넣어야 backfill 차단이 풀린다.
    """
    from skhy_research.adapters.persistence.db import build_engine
    from skhy_research.adapters.persistence.gate_decision_store import PostgresGateDecisionStore
    from skhy_research.adapters.persistence.schema import init_schema
    from skhy_research.application.gate_decision_seed import seed_confirmed_gate_decisions
    from skhy_research.application.gate_registry_loader import load_gate_registry

    settings: Settings = ctx.obj
    engine = build_engine(settings)
    init_schema(engine)
    store = PostgresGateDecisionStore(engine)

    outcomes = seed_confirmed_gate_decisions(store, recorded_at_utc=time.time_ns())
    for outcome in outcomes:
        typer.echo(f"seed {outcome.gate_id}: {outcome.action}")

    registry = load_gate_registry(store)
    as_of = time.time_ns()
    typer.echo("--- 런타임 gate 상태 ---")
    for outcome in outcomes:
        status = registry.effective_status(outcome.gate_id, as_of)
        blocks = registry.blocks(outcome.gate_id, as_of)
        typer.echo(f"{outcome.gate_id}: {status.value} (blocks={blocks})")
    engine.dispose()


@app.command("backfill")
def krx_backfill_command(
    ctx: typer.Context,
    end: str | None = typer.Option(
        None,
        "--end",
        help="마지막 KRX 기준일(YYYY-MM-DD). 기본값은 KST 어제",
    ),
    trading_days: int | None = typer.Option(
        None,
        "--trading-days",
        min=1,
        help="최근 수집할 최소 KRX 거래일 수. 기본값은 h1.min_krx_trading_days",
    ),
    symbols: str = typer.Option(
        "000660,005930",
        "--symbols",
        help="쉼표로 구분한 H1 기초자산 코드",
    ),
    pace_seconds: float = typer.Option(
        0.2,
        "--pace-seconds",
        min=0.0,
        help="KRX 날짜별 GET 사이 최소 지연(초)",
    ),
    max_rate_limit_retries: int = typer.Option(
        4,
        "--max-rate-limit-retries",
        min=0,
        help="ProviderRateLimitError 최대 재시도 수",
    ),
    live_crosscheck: bool = typer.Option(
        True,
        "--live-crosscheck/--no-live-crosscheck",
        help="KIS(prod) 주값과 Toss 대조값으로 최신 KRX 종가를 검증",
    ),
    live_move_bound_pct: str = typer.Option(
        "30",
        "--live-move-bound-pct",
        help="KRX 최신 종가 대비 live 값 이상치 bound(%)",
    ),
    cross_source_tolerance_pct: str = typer.Option(
        "1",
        "--cross-source-tolerance-pct",
        help="KIS와 Toss 현재가 괴리 허용치(%)",
    ),
) -> None:
    """KRX 무료 일별 API를 실제 백필하고 KIS/Toss로 read-only 대조한다."""

    from skhy_research.adapters.persistence.db import build_engine
    from skhy_research.adapters.persistence.schema import init_schema
    from skhy_research.adapters.providers.kis import KisReadOnlyClient
    from skhy_research.adapters.providers.krx import KrxReadOnlyClient
    from skhy_research.adapters.providers.toss import TossReadOnlyClient
    from skhy_research.adapters.secrets.factory import build_secret_provider
    from skhy_research.application.krx_backfill_runner import (
        DEFAULT_H1_BACKFILL_TARGETS,
        execute_krx_backfill,
    )
    from skhy_research.application.live_price_crosscheck import crosscheck_latest_prices

    settings: Settings = ctx.obj
    resolved_end = _parse_end_date(end)
    minimum = trading_days or settings.h1.min_krx_trading_days
    resolved_live_move_bound = _parse_nonnegative_decimal(
        live_move_bound_pct, "--live-move-bound-pct"
    )
    resolved_cross_source_tolerance = _parse_nonnegative_decimal(
        cross_source_tolerance_pct, "--cross-source-tolerance-pct"
    )
    requested_symbols = tuple(item.strip() for item in symbols.split(",") if item.strip())
    known_targets = {target.symbol: target for target in DEFAULT_H1_BACKFILL_TARGETS}
    unknown = sorted(set(requested_symbols) - set(known_targets))
    if not requested_symbols or unknown:
        raise typer.BadParameter(
            f"지원 symbol은 {sorted(known_targets)}이며 요청값={requested_symbols}, unknown={unknown}"
        )
    targets = tuple(known_targets[symbol] for symbol in requested_symbols)

    engine = build_engine(settings)
    init_schema(engine)
    secret_provider = build_secret_provider()
    krx_client = KrxReadOnlyClient(secret_provider)
    kis_client: KisReadOnlyClient | None = None
    toss_client: TossReadOnlyClient | None = None
    try:
        result = execute_krx_backfill(
            engine=engine,
            data_root=settings.data_root,
            client=krx_client,
            end=resolved_end,
            minimum_trading_days=minimum,
            targets=targets,
            min_request_interval_seconds=pace_seconds,
            max_rate_limit_retries=max_rate_limit_retries,
        )
        typer.echo("--- KRX 일별 실백필 ---")
        typer.echo(f"run_id={result.collection_run_id}")
        typer.echo(
            f"range={result.start.isoformat()}..{result.end.isoformat()} "
            f"trading_days={len(result.trading_dates)} date_requests={len(result.requested_dates)}"
        )
        typer.echo(
            f"raw_inserted={result.raw_inserted_count} raw_duplicate={result.raw_duplicate_count} "
            f"normalized_inserted={result.normalized_inserted_count} "
            f"normalized_duplicate={result.normalized_duplicate_count}"
        )
        typer.echo(f"snapshot_id={result.snapshot_id} path={result.snapshot_path}")
        for summary in result.instruments:
            coverage = summary.result.coverage
            typer.echo(
                f"{summary.symbol} {summary.instrument_id}: bars={summary.bar_count} "
                f"coverage={coverage.covered_trading_days}/{coverage.expected_trading_days} "
                f"complete={coverage.is_complete} latest_close={summary.latest_bar.close}"
            )

        if live_crosscheck:
            kis_client = KisReadOnlyClient(secret_provider, environment="prod")
            toss_client = TossReadOnlyClient(secret_provider)
            checks = crosscheck_latest_prices(
                result.instruments,
                kis_client=kis_client,
                toss_client=toss_client,
                max_live_move_pct=resolved_live_move_bound,
                max_cross_source_divergence_pct=resolved_cross_source_tolerance,
            )
            typer.echo("--- KIS(prod) 주값 / Toss 대조 read-only 교차검증 ---")
            typer.echo(
                "주의: KRX는 표시된 기준일의 공식 과거 종가이고, KIS/Toss는 호출 시점의 "
                "현재가(휴장 시 마지막 갱신값)라 동일값을 요구하지 않는다."
            )
            for check in checks:
                typer.echo(
                    f"{check.symbol}: status={check.status} "
                    f"KRX[{check.krx_basis_date}]={check.krx_official_close} "
                    f"KIS={check.kis_current_price} Toss={check.toss_current_price} "
                    f"KIS_vs_KRX={check.kis_vs_krx_move_pct:.4f}% "
                    f"Toss_vs_KRX={check.toss_vs_krx_move_pct:.4f}% "
                    f"KIS_vs_Toss={check.kis_vs_toss_divergence_pct:.4f}% "
                    f"reasons={list(check.anomaly_reasons)}"
                )
    finally:
        if kis_client is not None:
            kis_client.close()
        if toss_client is not None:
            toss_client.close()
        krx_client.close()
        engine.dispose()


@app.command("backfill-etp")
def krx_etp_backfill_command(
    ctx: typer.Context,
    trading_days: int | None = typer.Option(
        None,
        "--trading-days",
        min=1,
        help="기초자산 catalog의 최신 KRX 거래일 수. 기본값은 h1.min_krx_trading_days",
    ),
    pace_seconds: float = typer.Option(
        0.2,
        "--pace-seconds",
        min=0.0,
        help="KRX ETF/ETN GET 사이 최소 지연(초)",
    ),
    max_rate_limit_retries: int = typer.Option(
        4,
        "--max-rate-limit-retries",
        min=0,
        help="ProviderRateLimitError 최대 재시도 수",
    ),
) -> None:
    """daily-proxy용 KRX ETF/ETN NAV·IV·상장좌수를 read-only 백필한다."""

    from skhy_research.adapters.persistence.db import build_engine
    from skhy_research.adapters.persistence.schema import init_schema
    from skhy_research.adapters.providers.krx import KrxReadOnlyClient
    from skhy_research.adapters.secrets.factory import build_secret_provider
    from skhy_research.application.h1_daily_proxy_walk_forward import (
        load_latest_krx_daily_bars,
    )
    from skhy_research.application.krx_etp_backfill_runner import execute_krx_etp_backfill

    settings: Settings = ctx.obj
    minimum = trading_days or settings.h1.min_krx_trading_days
    engine = build_engine(settings)
    init_schema(engine)
    client = KrxReadOnlyClient(build_secret_provider())
    try:
        bars = load_latest_krx_daily_bars(engine, trading_days=minimum)
        result = execute_krx_etp_backfill(
            engine=engine,
            data_root=settings.data_root,
            client=client,
            trading_dates=(item.trading_date for item in bars),
            min_request_interval_seconds=pace_seconds,
            max_rate_limit_retries=max_rate_limit_retries,
        )
        typer.echo("--- KRX ETP daily-proxy 입력 백필 ---")
        typer.echo(f"run_id={result.collection_run_id}")
        typer.echo(
            f"range={result.trading_dates[0].isoformat()}.."
            f"{result.trading_dates[-1].isoformat()} trading_days={len(result.trading_dates)}"
        )
        typer.echo(
            f"raw_inserted={result.raw_inserted_count} "
            f"raw_duplicate={result.raw_duplicate_count} "
            f"normalized_inserted={result.normalized_inserted_count} "
            f"normalized_duplicate={result.normalized_duplicate_count}"
        )
        typer.echo(
            f"product_observations={result.product_observation_count} "
            f"excluded={result.excluded_observation_count} "
            f"symbols={list(result.product_symbols)}"
        )
    finally:
        client.close()
        engine.dispose()


@app.command("backtest")
def h1_daily_proxy_backtest_command(
    ctx: typer.Context,
    seed: int = typer.Option(7, "--seed", help="bootstrap·permutation·engine 결정론 seed"),
    trading_days: int | None = typer.Option(
        None,
        "--trading-days",
        min=1,
        help="최신 KRX 거래일 수. 기본값은 h1.min_krx_trading_days",
    ),
    kappa: str = typer.Option("0.10", "--kappa", help="명시적 daily-proxy 전이계수"),
    neutral_band: str = typer.Option(
        "0.001", "--neutral-band", help="daily-proxy 무신호 중립 구간"
    ),
    bootstrap_resamples: int = typer.Option(
        1000, "--bootstrap-resamples", min=1, help="기대값 bootstrap 재표본 횟수"
    ),
    permutations: int = typer.Option(
        1000, "--permutations", min=1, help="날짜 부호 permutation 횟수"
    ),
    json_output: bool = typer.Option(False, "--json", help="결과를 JSON으로 출력"),
) -> None:
    """실 KRX daily-proxy의 60/30/30·walk-forward 연구 백테스트를 실행한다."""

    import json

    from skhy_research.adapters.persistence.db import build_engine
    from skhy_research.adapters.persistence.schema import init_schema
    from skhy_research.application.h1_daily_proxy_walk_forward import (
        DailyProxyBacktestConfig,
        run_h1_daily_proxy_walk_forward,
    )

    settings: Settings = ctx.obj
    config = DailyProxyBacktestConfig(
        seed=seed,
        trading_days=trading_days or settings.h1.min_krx_trading_days,
        kappa=_parse_nonnegative_decimal(kappa, "--kappa"),
        neutral_band=_parse_nonnegative_decimal(neutral_band, "--neutral-band"),
        bootstrap_resamples=bootstrap_resamples,
        permutation_count=permutations,
    )
    engine = build_engine(settings)
    init_schema(engine)
    try:
        result = run_h1_daily_proxy_walk_forward(engine, settings, config)
    finally:
        engine.dispose()

    if json_output:
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return

    typer.echo("--- H1 KRX daily-proxy walk-forward ---")
    typer.echo(
        f"model={result.promotion.model_version} scope={result.promotion.promotion_scope} "
        f"promotion_eligible={result.promotion.promotion_eligible}"
    )
    typer.echo(
        f"bars={result.bar_count} etp_snapshots={result.etp_snapshot_count} "
        f"features={result.available_feature_count}"
    )
    for split in result.chronological_splits:
        typer.echo(f"split {split.name}: {split.start.isoformat()}..{split.end.isoformat()}")
    for fold in result.folds:
        typer.echo(
            f"fold={fold.fold_number} train={fold.train.start.isoformat()}.."
            f"{fold.train.end.isoformat()} test={fold.test.start.isoformat()}.."
            f"{fold.test.end.isoformat()} trades={fold.base.trade_count} "
            f"base_expectancy={fold.base.expectancy} base_pf={fold.base.profit_factor} "
            f"base_mdd={fold.base.max_drawdown} "
            f"stress_pnl={fold.stress_2x.cumulative_pnl} "
            f"ci={fold.base.bootstrap_expectancy_ci} "
            f"permutation_p={fold.base.permutation_p_value}"
        )
    typer.echo(
        f"aggregate trades={result.aggregate_base.trade_count} "
        f"base_pnl={result.aggregate_base.cumulative_pnl} "
        f"base_expectancy={result.aggregate_base.expectancy} "
        f"base_pf={result.aggregate_base.profit_factor} "
        f"base_mdd={result.aggregate_base.max_drawdown} "
        f"stress_2x_pnl={result.aggregate_stress_2x.cumulative_pnl}"
    )
    typer.echo(
        f"promotion={result.promotion.verdict.value} reasons={list(result.promotion.reasons)}"
    )
    typer.echo(f"data_snapshot_hash={result.data_snapshot_hash}")
    typer.echo(f"result_hash={result.result_hash}")


@app.command("prefalsification-study")
def h1_prefalsification_study_command(
    ctx: typer.Context,
    input_json: Annotated[
        Path | None,
        typer.Option(
            "--input-json",
            help="lineage·시각이 포함된 KRX 사전반증 일별 JSON. 생략 시 기존 data catalog만 감사",
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="JSON/Markdown 리포트 디렉터리. 기본값은 var/reports/h1_prefalsification",
        ),
    ] = None,
    seed: int = typer.Option(7, "--seed", help="permutation·block bootstrap 결정론 seed"),
    permutations: int = typer.Option(
        2000, "--permutations", min=1, help="날짜 permutation 횟수"
    ),
    bootstrap_resamples: int = typer.Option(
        2000, "--bootstrap-resamples", min=1, help="거래일 block bootstrap 횟수"
    ),
    weak_daily_v1: bool = typer.Option(
        False,
        "--weak-daily-v1",
        help="실 KRX 조회 전용 API로 3년 weak_daily_v1 입력을 수집·실행",
    ),
    end: str | None = typer.Option(
        None, "--end", help="weak 수집 종료일(YYYY-MM-DD). 기본: 어제"
    ),
    minimum_trading_days: int = typer.Option(
        756,
        "--minimum-trading-days",
        min=1,
        help="weak 수집 목표 거래일(기본 756, PRD 10.2 3년 표본)",
    ),
) -> None:
    """무료 KRX 일별 proxy로 H1 라이브 수집 착수 전 사전반증을 실행한다."""

    from skhy_research.application.h1_prefalsification_study import (
        PrefalsificationStudyConfig,
        audit_existing_krx_daily_data,
        build_data_unavailable_result,
        collect_krx_weak_daily_inputs,
        load_prefalsification_observations_json,
        run_prefalsification_study,
        run_weak_daily_prefalsification_study,
        write_prefalsification_reports,
    )

    settings: Settings = ctx.obj
    config = PrefalsificationStudyConfig(
        seed=seed,
        permutations=permutations,
        bootstrap_resamples=bootstrap_resamples,
    )
    if weak_daily_v1:
        if input_json is not None:
            raise typer.BadParameter(
                "--weak-daily-v1과 --input-json은 동시에 사용할 수 없다"
            )
        from skhy_research.adapters.providers.krx import KrxReadOnlyClient
        from skhy_research.adapters.secrets.factory import build_secret_provider

        resolved_end = _parse_end_date(end)
        snapshot_path = (
            settings.data_root
            / "historical"
            / "h1_prefalsification"
            / "weak_daily_v1"
            / f"krx_weak_daily_inputs_{resolved_end:%Y%m%d}.json"
        )
        client = KrxReadOnlyClient(build_secret_provider())
        try:
            collection = collect_krx_weak_daily_inputs(
                client,
                end=resolved_end,
                output_path=snapshot_path,
                minimum_trading_days=minimum_trading_days,
            )
        finally:
            client.close()
        result = run_weak_daily_prefalsification_study(
            collection.observations,
            config,
            availability_audit=collection.availability_audit,
        )
    elif input_json is None:
        result = build_data_unavailable_result(
            audit_existing_krx_daily_data(settings.data_root)
        )
    else:
        observations = load_prefalsification_observations_json(input_json)
        result = run_prefalsification_study(observations, config)
    resolved_output = output_dir or settings.var_root / "reports" / "h1_prefalsification"
    basename = (
        f"h1_prefalsification_{result.model_variant.value}_"
        f"{result.data_snapshot_hash[:12]}"
    )
    json_path, markdown_path = write_prefalsification_reports(
        result, resolved_output, basename=basename
    )
    typer.echo("--- H1 historical pre-falsification study ---")
    typer.echo(
        f"model_variant={result.model_variant.value} status={result.status.value} "
        f"verdict={result.verdict.value} "
        f"scope={result.promotion_scope} paper_only={result.paper_only}"
    )
    typer.echo(
        f"scheduled={result.scheduled_observations} raw={result.raw_eligible_count} "
        f"controlled={result.controlled_eligible_count}"
    )
    for label, model in (("raw", result.raw_model), ("controlled", result.controlled_model)):
        if model is None:
            typer.echo(f"{label}=NOT_COMPUTABLE")
            continue
        statistics = model.statistics
        typer.echo(
            f"{label}: beta={statistics.beta} hac_t={statistics.t_statistic} "
            f"permutation_p={statistics.permutation_p_value} "
            f"block_ci={statistics.block_bootstrap_ci} verdict={model.verdict.value}"
        )
    typer.echo(f"reasons={list(result.reasons)}")
    typer.echo(f"json_report={json_path}")
    typer.echo(f"markdown_report={markdown_path}")


def _parse_end_date(value: str | None) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date() - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("--end는 YYYY-MM-DD여야 한다") from exc


def _parse_nonnegative_decimal(value: str, option_name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise typer.BadParameter(f"{option_name}은 0 이상의 숫자여야 한다") from exc
    if parsed < 0 or not parsed.is_finite():
        raise typer.BadParameter(f"{option_name}은 0 이상의 유한 숫자여야 한다")
    return parsed


@app.command("version")
def version() -> None:
    """패키지 버전을 출력한다."""
    from importlib.metadata import version as pkg_version

    typer.echo(pkg_version("skhy-research"))


if __name__ == "__main__":
    app()
