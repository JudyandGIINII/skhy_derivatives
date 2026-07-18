"""P0-01 완료 검증: 실제 키 없이 config loader가 재현 가능한 결과를 낸다."""

from __future__ import annotations

import pytest

from skhy_research.application.config import load_settings


def test_local_settings_load_with_expected_defaults() -> None:
    settings = load_settings("local")

    assert settings.broker_mode == "paper"
    assert settings.risk.max_risk_per_trade_pct == pytest.approx(0.25)
    assert settings.risk.max_daily_loss_pct == pytest.approx(1.0)
    assert settings.risk.max_cumulative_mdd_pct == pytest.approx(5.0)
    assert settings.risk.h1_quote_max_age_seconds == pytest.approx(2)
    assert settings.risk.h2h3_quote_max_age_seconds == pytest.approx(5)
    assert settings.h1.signal_snapshot_time_kst == "15:10:00"
    assert settings.h1.order_intent_cutoff_kst == "15:19:30"
    assert settings.h1.min_krx_trading_days == 120
    assert settings.h1.split_train_days == 60
    assert settings.h1.split_validation_days == 30
    assert settings.h1.split_test_days == 30
    assert settings.h2.common_ratio_common_to_adr == 10
    assert settings.h2.min_forward_paper_trading_days == 60
    assert settings.h2.min_qualified_signals == 30
    assert settings.h3.skhy_trading_start_date == "2026-07-10"
    assert settings.h3.max_holding_minutes == 30
    assert settings.promotion.min_profit_factor == pytest.approx(1.2)
    assert settings.promotion.max_strategy_mdd_pct == pytest.approx(5.0)
    assert settings.promotion.max_single_day_profit_share == pytest.approx(0.30)


def test_config_hash_is_deterministic_for_same_env() -> None:
    a = load_settings("local")
    b = load_settings("local")
    assert a.config_hash == b.config_hash


def test_config_hash_differs_across_environments() -> None:
    local = load_settings("local")
    ci = load_settings("ci")
    assert local.config_hash != ci.config_hash


def test_config_hash_excludes_secret_fields() -> None:
    settings = load_settings("local")
    dumped = settings.canonical_dict()
    serialized = str(dumped).lower()
    for forbidden in ("api_key", "secret", "token", "password"):
        assert forbidden not in serialized


def test_non_paper_broker_mode_is_rejected(tmp_path, monkeypatch) -> None:
    configs_dir = tmp_path / "configs"
    (configs_dir / "environments").mkdir(parents=True)
    (configs_dir / "base.yaml").write_text(
        """
broker_mode: live
risk:
  max_risk_per_trade_pct: 0.25
  max_daily_loss_pct: 1.0
  max_cumulative_mdd_pct: 5.0
  extended_hours_order_type: LIMIT
  h1_quote_max_age_seconds: 2
  h2h3_quote_max_age_seconds: 5
  leg_timeout_seconds: 5
promotion:
  min_expectancy: 0.0
  min_profit_factor: 1.2
  stress_min_cumulative_pnl: 0.0
  max_single_day_profit_share: 0.3
  max_strategy_mdd_pct: 5.0
h1:
  signal_snapshot_time_kst: "15:10:00"
  order_intent_cutoff_kst: "15:19:30"
  entry_window_end_kst: "15:20:00"
  min_krx_trading_days: 120
  split_train_days: 60
  split_validation_days: 30
  split_test_days: 30
h2:
  common_ratio_common_to_adr: 10
  min_forward_paper_trading_days: 60
  min_qualified_signals: 30
h3:
  common_ratio_common_to_adr: 10
  sync_sample_windows_seconds: [1, 5, 60]
  max_holding_minutes: 30
  min_forward_paper_trading_days: 60
  min_qualified_signals: 30
  skhy_trading_start_date: "2026-07-10"
quality:
  stale_reference: true
  provider_divergence_blocks_signal: true
""",
        encoding="utf-8",
    )
    (configs_dir / "environments" / "local.yaml").write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="paper"):
        load_settings("local", configs_dir=configs_dir)


def test_settings_is_frozen() -> None:
    settings = load_settings("local")
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        settings.broker_mode = "live"  # type: ignore[misc]
