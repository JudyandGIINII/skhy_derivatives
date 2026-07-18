"""P0-02 단위 검증: manifest가 결정론적 필드를 만들고 불변임을 확인한다."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from skhy_research.domain.experiment import build_manifest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_build_manifest_captures_repo_and_config_state() -> None:
    manifest = build_manifest(
        config_env="local",
        config_hash="deadbeef",
        seed=42,
        component_versions={"strategy.h1": "1.0.0", "fill_model": "1.0.0"},
        repo_root=_REPO_ROOT,
    )

    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    expected_lockfile_hash = hashlib.sha256((_REPO_ROOT / "uv.lock").read_bytes()).hexdigest()

    assert manifest.repo_commit == expected_commit
    assert manifest.lockfile_hash == expected_lockfile_hash
    assert manifest.config_env == "local"
    assert manifest.config_hash == "deadbeef"
    assert manifest.seed == 42
    assert manifest.component_versions == {"fill_model": "1.0.0", "strategy.h1": "1.0.0"}
    assert manifest.ended_at_utc is None
    assert manifest.data_snapshot_id is None
    assert manifest.run_id


def test_manifest_finalize_is_immutable_copy() -> None:
    manifest = build_manifest(
        config_env="local",
        config_hash="deadbeef",
        seed=1,
        component_versions={},
        repo_root=_REPO_ROOT,
    )
    finalized = manifest.finalize(ended_at_utc=123, data_snapshot_id="snap-1")

    assert manifest.ended_at_utc is None  # 원본은 변경되지 않는다
    assert finalized.ended_at_utc == 123
    assert finalized.data_snapshot_id == "snap-1"
    assert finalized.run_id == manifest.run_id
