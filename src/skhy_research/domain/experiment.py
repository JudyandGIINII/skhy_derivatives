"""실행 manifest와 lineage/execution edge 도메인 계약 (PRD 4.8, 8.1, 13.1).

모든 실행에는 run_id가 부여되고, 재현에 필요한 commit·lockfile·config·seed·
데이터 snapshot이 immutable manifest로 저장된다. lineage edge는
raw -> normalized -> feature -> signal 계보를, execution edge는
signal -> risk decision -> order -> fill -> position/PnL 계보를 추적한다.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
import uuid
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skhy_research.domain.enums import PromotionVerdict


class ExecutionManifest(BaseModel):
    """FR-01, FR-16: 실행 결과에 저장소 commit·config hash·data snapshot을 남긴다."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    repo_commit: str
    repo_dirty: bool
    python_version: str
    lockfile_hash: str
    config_env: str
    config_hash: str
    component_versions: dict[str, str] = Field(default_factory=dict)
    seed: int
    started_at_utc: int  # UTC epoch nanoseconds
    ended_at_utc: int | None = None
    data_snapshot_id: str | None = None

    def finalize(self, ended_at_utc: int, data_snapshot_id: str | None = None) -> ExecutionManifest:
        return self.model_copy(
            update={"ended_at_utc": ended_at_utc, "data_snapshot_id": data_snapshot_id}
        )


class LineageEdge(BaseModel):
    """raw -> normalized -> feature -> signal 계보 한 단계."""

    model_config = ConfigDict(frozen=True)

    edge_id: str
    run_id: str
    parent_record_id: str
    parent_layer: str  # raw|normalized|feature|signal
    child_record_id: str
    child_layer: str
    algorithm_version: str
    created_at_utc: int


class ExecutionEdge(BaseModel):
    """signal -> risk decision -> order -> fill -> position/PnL 계보 한 단계."""

    model_config = ConfigDict(frozen=True)

    edge_id: str
    run_id: str
    signal_id: str | None = None
    risk_decision_id: str | None = None
    order_id: str | None = None
    fill_id: str | None = None
    position_update_id: str | None = None
    created_at_utc: int


class ExperimentResult(BaseModel):
    """PRD 8.2 ExperimentResult: 데이터 버전·분할·비용 가정·지표·CI·스트레스와 최종 판정."""

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    run_id: str
    strategy_id: str
    strategy_version: str
    data_snapshot_id: str
    split_name: str  # train|validation|test|walk_forward_<n>
    cost_scenario: str  # base|stress_2x|liquidity_half 등
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    confidence_intervals: dict[str, tuple[Decimal, Decimal]] = Field(default_factory=dict)
    verdict: PromotionVerdict
    verdict_reason: str
    created_at_utc: int
    model_version: str = "unspecified"
    data_resolution: str = "unspecified"
    promotion_scope: str = "strategy-default"
    promotion_eligible: bool = True

    @model_validator(mode="after")
    def _ineligible_model_cannot_be_reported_as_pass(self) -> ExperimentResult:
        if not self.promotion_eligible and self.verdict == PromotionVerdict.PASS:
            raise ValueError(
                "promotion_eligible=False인 모델은 PASS 결과로 기록할 수 없다: "
                f"scope={self.promotion_scope}, model={self.model_version}"
            )
        return self


def _git_output(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def build_manifest(
    config_env: str,
    config_hash: str,
    seed: int,
    component_versions: dict[str, str],
    repo_root: Path,
) -> ExecutionManifest:
    """새 실행의 manifest를 만든다. commit/lockfile/config hash가 모두 결정론적이어야 한다.

    도메인 계층은 `Settings`(application 계층)에 의존하지 않는다. 호출자가
    `settings.env_name`/`settings.config_hash`를 직접 전달한다.
    """
    repo_commit = _git_output(repo_root, "rev-parse", "HEAD")
    dirty_status = _git_output(repo_root, "status", "--porcelain")
    lockfile_path = repo_root / "uv.lock"
    lockfile_hash = hashlib.sha256(lockfile_path.read_bytes()).hexdigest()

    import platform

    return ExecutionManifest(
        run_id=str(uuid.uuid4()),
        repo_commit=repo_commit,
        repo_dirty=bool(dirty_status),
        python_version=platform.python_version(),
        lockfile_hash=lockfile_hash,
        config_env=config_env,
        config_hash=config_hash,
        component_versions=dict(sorted(component_versions.items())),
        seed=seed,
        started_at_utc=time.time_ns(),
    )
