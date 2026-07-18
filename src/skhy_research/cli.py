"""수집·백필·검증·백테스트·페이퍼 실행·리포트용 CLI 엔트리포인트.

모든 서브커맨드는 콜백에서 먼저 부팅 게이트(bootstrap)를 통과해야 한다.
Phase 1의 KRX 실백필은 gate journal을 먼저 확인하고 조회 전용 provider만 조립한다.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
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
