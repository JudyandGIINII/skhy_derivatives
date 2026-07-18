"""수집·백필·검증·백테스트·페이퍼 실행·리포트용 CLI 엔트리포인트.

모든 서브커맨드는 콜백에서 먼저 부팅 게이트(bootstrap)를 통과해야 한다.
Phase 0에서는 config 점검 명령만 제공한다. 이후 Phase에서 ingest/backfill/
backtest/paper/report 서브커맨드를 추가한다.
"""

from __future__ import annotations

import typer

from skhy_research.application.boot import bootstrap
from skhy_research.application.config import Settings, load_settings

app = typer.Typer(help="SK하이닉스 구조적 수급·교차시장 상대가치 연구 시스템 CLI")


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


@app.command("version")
def version() -> None:
    """패키지 버전을 출력한다."""
    from importlib.metadata import version as pkg_version

    typer.echo(pkg_version("skhy-research"))


if __name__ == "__main__":
    app()
