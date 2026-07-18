"""P1-07 검증: 같은 (전략버전, split)에 다른 seed로 재실행하는 것을 차단한다."""

from __future__ import annotations

import pytest

from skhy_research.experiments.seed_registry import DuplicateExperimentRunError, SeedRegistry


def test_first_registration_succeeds_and_is_retrievable() -> None:
    registry = SeedRegistry()
    registry.register_run("1.0.0", "test", seed=42)
    assert registry.get_seed("1.0.0", "test") == 42


def test_reregistering_same_seed_is_idempotent() -> None:
    registry = SeedRegistry()
    registry.register_run("1.0.0", "test", seed=42)
    registry.register_run("1.0.0", "test", seed=42)  # 재현성 검증을 위한 재실행 — 허용
    assert registry.get_seed("1.0.0", "test") == 42


def test_registering_different_seed_for_same_key_raises() -> None:
    registry = SeedRegistry()
    registry.register_run("1.0.0", "test", seed=42)
    with pytest.raises(DuplicateExperimentRunError):
        registry.register_run("1.0.0", "test", seed=99)


def test_different_splits_and_versions_are_independent() -> None:
    registry = SeedRegistry()
    registry.register_run("1.0.0", "train", seed=1)
    registry.register_run("1.0.0", "test", seed=2)
    registry.register_run("1.1.0", "test", seed=3)

    assert registry.get_seed("1.0.0", "train") == 1
    assert registry.get_seed("1.0.0", "test") == 2
    assert registry.get_seed("1.1.0", "test") == 3


def test_get_seed_returns_none_for_unknown_key() -> None:
    registry = SeedRegistry()
    assert registry.get_seed("1.0.0", "test") is None
