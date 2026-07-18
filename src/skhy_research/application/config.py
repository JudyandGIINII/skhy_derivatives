"""환경별 설정 로더.

base.yaml + configs/environments/{SKHY_ENV}.yaml + 환경변수를 병합해
불변 Settings를 만든다. config_hash는 비밀값을 제외한 canonical 표현의
sha256이며 실행 manifest(P0-02)에 사용된다.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIGS_DIR = _REPO_ROOT / "configs"

_ALLOWED_BROKER_MODES = frozenset({"paper"})


class RiskLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_cumulative_mdd_pct: float
    extended_hours_order_type: str
    h1_quote_max_age_seconds: float
    h2h3_quote_max_age_seconds: float
    leg_timeout_seconds: float


class PromotionCriteria(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_expectancy: float
    min_profit_factor: float
    stress_min_cumulative_pnl: float
    max_single_day_profit_share: float
    max_strategy_mdd_pct: float


class H1Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal_snapshot_time_kst: str
    order_intent_cutoff_kst: str
    entry_window_end_kst: str
    min_krx_trading_days: int
    split_train_days: int
    split_validation_days: int
    split_test_days: int


class H2Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    common_ratio_common_to_adr: int
    min_forward_paper_trading_days: int
    min_qualified_signals: int


class H3Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    common_ratio_common_to_adr: int
    sync_sample_windows_seconds: tuple[int, ...]
    max_holding_minutes: int
    min_forward_paper_trading_days: int
    min_qualified_signals: int
    skhy_trading_start_date: str


class QualityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    stale_reference: bool
    provider_divergence_blocks_signal: bool


class Settings(BaseModel):
    """비밀값을 포함하지 않는 불변 설정. 비밀값은 SecretProvider(P0-03)를 통해서만 조회한다."""

    model_config = ConfigDict(frozen=True)

    env_name: str
    broker_mode: str
    database_url_env: str
    data_root: Path
    var_root: Path
    risk: RiskLimits
    promotion: PromotionCriteria
    h1: H1Config
    h2: H2Config
    h3: H3Config
    quality: QualityConfig
    cost_stress_multiplier: float
    liquidity_stress_divisor: float

    def canonical_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        return dict(sorted(data.items()))

    @property
    def config_hash(self) -> str:
        canonical = json.dumps(self.canonical_dict(), ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}는 매핑(mapping)이어야 한다")
    return loaded


def load_settings(env_name: str | None = None, configs_dir: Path | None = None) -> Settings:
    """SKHY_ENV(기본 local)에 맞는 병합 설정을 로드한다.

    broker_mode가 paper가 아니면 즉시 예외를 발생시켜 부팅을 차단한다 (P0-03 게이트).
    """
    resolved_env = env_name or os.environ.get("SKHY_ENV", "local")
    base_dir = configs_dir or _CONFIGS_DIR

    merged = _load_yaml(base_dir / "base.yaml")
    env_overrides = _load_yaml(base_dir / "environments" / f"{resolved_env}.yaml")
    merged = _deep_merge(merged, env_overrides)

    broker_mode = merged.get("broker_mode", "paper")
    if broker_mode not in _ALLOWED_BROKER_MODES:
        raise RuntimeError(
            f"허용되지 않은 broker_mode='{broker_mode}'. v1은 'paper'만 등록한다 (PRD 7.3, 13.3)."
        )

    settings = Settings(
        env_name=resolved_env,
        broker_mode=broker_mode,
        database_url_env=merged.get("database_url_env", "SKHY_DATABASE_URL"),
        data_root=Path(merged.get("data_root", "./data")),
        var_root=Path(merged.get("var_root", "./var")),
        risk=RiskLimits(**merged["risk"]),
        promotion=PromotionCriteria(**merged["promotion"]),
        h1=H1Config(**merged["h1"]),
        h2=H2Config(**merged["h2"]),
        h3=H3Config(**merged["h3"]),
        quality=QualityConfig(**merged["quality"]),
        cost_stress_multiplier=merged.get("cost_stress_multiplier", 2.0),
        liquidity_stress_divisor=merged.get("liquidity_stress_divisor", 2.0),
    )
    return settings


@lru_cache(maxsize=8)
def get_settings(env_name: str | None = None) -> Settings:
    return load_settings(env_name)
