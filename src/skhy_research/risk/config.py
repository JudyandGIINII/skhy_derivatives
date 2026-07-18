"""리스크 정책 YAML 로더."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from skhy_research.risk.models import RiskPolicy


def load_risk_policy(path: Path) -> RiskPolicy:
    """명시한 정책 파일을 로드한다. 누락·형식 오류는 기본값으로 숨기지 않는다."""

    if not path.is_file():
        raise FileNotFoundError(f"리스크 정책 파일이 없음: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload: Any = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"리스크 정책은 mapping이어야 한다: {path}")
    return RiskPolicy.model_validate(payload)
