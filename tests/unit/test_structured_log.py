"""P0-10 검증: 구조화 JSON 로그가 유효한 JSON이며 마스킹과 함께 동작한다."""

from __future__ import annotations

import io
import json
import logging

import pytest

from skhy_research.observability.masking import (
    clear_registered_secrets,
    install_masking_filter,
    register_secret,
    uninstall_masking_filter,
)
from skhy_research.observability.structured_log import StructuredJsonFormatter, log_structured


@pytest.fixture(autouse=True)
def _isolated_state():
    clear_registered_secrets()
    uninstall_masking_filter()
    yield
    clear_registered_secrets()
    uninstall_masking_filter()


def _logger_with_json_handler(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredJsonFormatter())
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, stream


def test_structured_log_output_is_valid_json_with_expected_fields() -> None:
    logger, stream = _logger_with_json_handler("test.structured.basic")
    log_structured(logger, logging.INFO, "provider event", provider="kis", latency_ms=180.5)

    parsed = json.loads(stream.getvalue())
    assert parsed["message"] == "provider event"
    assert parsed["level"] == "INFO"
    assert parsed["provider"] == "kis"
    assert parsed["latency_ms"] == 180.5
    assert "timestamp_utc_ns" in parsed


def test_structured_log_masks_registered_secrets() -> None:
    install_masking_filter()
    register_secret("canary-structured-log-secret")
    logger, stream = _logger_with_json_handler("test.structured.masking")

    log_structured(logger, logging.ERROR, "auth failed", key="canary-structured-log-secret")

    output = stream.getvalue()
    assert "canary-structured-log-secret" not in output
    assert "***MASKED***" in output
    parsed = json.loads(output)  # 마스킹 후에도 여전히 유효한 JSON이어야 한다
    assert parsed["level"] == "ERROR"
