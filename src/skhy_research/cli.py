"""수집·백필·검증·백테스트·페이퍼 실행·리포트용 CLI 엔트리포인트.

모든 서브커맨드는 콜백에서 먼저 부팅 게이트(bootstrap)를 통과해야 한다.
Phase 0에서는 config 점검 명령만 제공한다. 이후 Phase에서 ingest/backfill/
backtest/paper/report 서브커맨드를 추가한다.
"""

from __future__ import annotations

import time

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


@app.command("version")
def version() -> None:
    """패키지 버전을 출력한다."""
    from importlib.metadata import version as pkg_version

    typer.echo(pkg_version("skhy-research"))


if __name__ == "__main__":
    app()
