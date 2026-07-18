"""P0-03 완료 검증: 등록된 비밀값이 마스킹 없이는 어디에도 노출되지 않는다."""

from __future__ import annotations

import io
import logging

import pytest

from skhy_research.observability.masking import (
    clear_registered_secrets,
    install_masking_filter,
    mask,
    mask_exception,
    register_secret,
    uninstall_masking_filter,
)


@pytest.fixture(autouse=True)
def _isolated_masking_state():
    clear_registered_secrets()
    uninstall_masking_filter()
    yield
    clear_registered_secrets()
    uninstall_masking_filter()


def test_mask_replaces_registered_secret_only() -> None:
    register_secret("sk-canary-abc123")
    text = "요청 실패: token=sk-canary-abc123, path=/v1/quotes"

    masked = mask(text)

    assert "sk-canary-abc123" not in masked
    assert "***MASKED***" in masked
    assert "/v1/quotes" in masked  # 무관한 텍스트는 보존


def test_mask_ignores_short_values_to_avoid_over_masking() -> None:
    register_secret("ab")  # 최소 길이 미만
    text = "ab는 매우 흔한 부분 문자열일 수 있다"

    masked = mask(text)

    assert masked == text


def test_mask_exception_scrubs_secret_in_str() -> None:
    register_secret("super-secret-value-999")
    exc = ValueError("인증 실패: key=super-secret-value-999")

    assert "super-secret-value-999" not in mask_exception(exc)
    assert "***MASKED***" in mask_exception(exc)


def test_install_masking_filter_scrubs_records_from_any_logger() -> None:
    """child logger에서 발생해 root handler로 전파된 레코드도 마스킹되어야 한다."""
    register_secret("leaked-canary-token")
    install_masking_filter()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    child_logger = logging.getLogger("app.provider.kis")
    child_logger.setLevel(logging.INFO)
    child_logger.propagate = True
    child_logger.info("api call failed: token=%s", "leaked-canary-token")

    root.removeHandler(handler)
    output = stream.getvalue()

    assert "leaked-canary-token" not in output
    assert "***MASKED***" in output


def test_install_masking_filter_is_idempotent() -> None:
    install_masking_filter()
    factory_after_first = logging.getLogRecordFactory()

    install_masking_filter()
    factory_after_second = logging.getLogRecordFactory()

    assert factory_after_first is factory_after_second
