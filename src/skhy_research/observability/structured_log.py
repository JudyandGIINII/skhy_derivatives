"""구조화 JSON 로그 포맷터 (PRD 13.4, `implementation_plan.md` 2.1 관측성).

`record.msg`는 `application.boot.bootstrap()`이 설치하는 마스킹 record factory가
이미 정리한다. `structured_fields`의 문자열 값은 record factory가 보지 못하는
custom extra이므로, 이 formatter가 직접 `mask()`를 한 번 더 적용해 JSON
직렬화 결과에도 비밀값이 남지 않게 한다.
"""

from __future__ import annotations

import json
import logging
import time

from skhy_research.observability.masking import mask


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp_utc_ns": time.time_ns(),
            "level": record.levelname,
            "logger": record.name,
            "message": mask(record.getMessage()),
        }
        extra = getattr(record, "structured_fields", None)
        if isinstance(extra, dict):
            payload.update({k: mask(v) if isinstance(v, str) else v for k, v in extra.items()})
        if record.exc_info:
            payload["exception"] = mask(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def install_structured_logging(logger: logging.Logger | None = None, level: int = logging.INFO) -> None:
    target = logger if logger is not None else logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    target.handlers = [handler]
    target.setLevel(level)


def log_structured(logger: logging.Logger, level: int, message: str, **fields: object) -> None:
    logger.log(level, message, extra={"structured_fields": fields})
