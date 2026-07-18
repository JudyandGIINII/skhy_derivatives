"""P0-03: bootstrap()이 마스킹을 설치하고 부팅 이후 로그에 비밀값을 남기지 않는지 검증."""

from __future__ import annotations

import io
import logging

import pytest

from skhy_research.adapters.secrets.env_secret_provider import EnvSecretProvider
from skhy_research.application.boot import bootstrap
from skhy_research.application.config import load_settings
from skhy_research.observability.masking import (
    clear_registered_secrets,
    uninstall_masking_filter,
)


@pytest.fixture(autouse=True)
def _isolated_logging_state():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    clear_registered_secrets()
    uninstall_masking_filter()
    yield
    root.handlers = original_handlers
    root.level = original_level
    clear_registered_secrets()
    uninstall_masking_filter()


def test_bootstrap_installs_masking_record_factory() -> None:
    settings = load_settings("local")

    bootstrap(settings)

    assert logging.getLogRecordFactory().__name__ == "_masking_record_factory"


def test_bootstrap_then_secret_leak_attempt_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIS_APP_SECRET", "canary-boot-secret-555")
    settings = load_settings("local")
    bootstrap(settings)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    provider = EnvSecretProvider()
    secret = provider.get_secret("KIS_APP_SECRET")
    logging.getLogger("app.provider.kis").error("provider auth failed with key=%s", secret)

    output = stream.getvalue()
    assert "canary-boot-secret-555" not in output
    assert "***MASKED***" in output
