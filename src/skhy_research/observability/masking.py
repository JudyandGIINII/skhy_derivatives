"""로그·예외에서 비밀값을 제거하는 마스킹 레지스트리 (PRD 7.3, 13.3).

`SecretProvider` 구현은 값을 반환하기 직전에 `register_secret()`을 호출해야
한다. `install_masking_filter()`는 `logging.LogRecordFactory`를 교체해
프로세스 전역에서 마스킹을 적용한다. logger별 `Filter`를 개별 로거에
붙이는 방식은 상위(root) 로거로 전파(propagate)된 레코드에는 적용되지
않는다는 표준 logging 모듈의 한계가 있어(Filter는 레코드를 만든 로거에서만
평가됨) 채택하지 않는다. record factory 교체는 로거 계층과 무관하게
레코드 생성 시점에 한 번만 마스킹하면 되므로 이 한계가 없다.
"""

from __future__ import annotations

import logging
import threading

_MASK_TOKEN = "***MASKED***"
_MIN_SECRET_LENGTH = 4  # 너무 짧은 값(예: "1")까지 마스킹하면 로그가 무의미해진다

_lock = threading.Lock()
_secret_values: set[str] = set()

_original_record_factory = logging.getLogRecordFactory()
_masking_installed = False


def register_secret(value: str | None) -> None:
    if not value or len(value) < _MIN_SECRET_LENGTH:
        return
    with _lock:
        _secret_values.add(value)


def clear_registered_secrets() -> None:
    """테스트 격리 전용. 운영 코드에서 호출하지 않는다."""
    with _lock:
        _secret_values.clear()


def mask(text: str) -> str:
    if not text:
        return text
    with _lock:
        secrets = sorted(_secret_values, key=len, reverse=True)
    masked = text
    for secret in secrets:
        if secret in masked:
            masked = masked.replace(secret, _MASK_TOKEN)
    return masked


def mask_exception(exc: BaseException) -> str:
    return mask(str(exc))


def _masking_record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
    record = _original_record_factory(*args, **kwargs)  # type: ignore[arg-type]
    record.msg = mask(record.getMessage())
    record.args = ()
    return record


def install_masking_filter() -> None:
    """프로세스 전역 LogRecordFactory를 마스킹 버전으로 교체한다 (idempotent)."""
    global _masking_installed
    if _masking_installed:
        return
    logging.setLogRecordFactory(_masking_record_factory)
    _masking_installed = True


def uninstall_masking_filter() -> None:
    """테스트 격리 전용."""
    global _masking_installed
    logging.setLogRecordFactory(_original_record_factory)
    _masking_installed = False
