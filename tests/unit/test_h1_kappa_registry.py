"""P1-03 검증: kappa는 학습 구간에서만 설정되며 기본값(예: 1)을 절대 반환하지 않는다."""

from __future__ import annotations

from decimal import Decimal

from skhy_research.features.h1_close_pressure.kappa_registry import KappaRegistry


def test_unset_kappa_returns_none_not_a_default() -> None:
    registry = KappaRegistry()
    assert registry.get_kappa("HKEX_7709", "normal", version="1.0.0") is None


def test_set_and_get_kappa_round_trip() -> None:
    registry = KappaRegistry()
    registry.set_kappa("HKEX_7709", "high_vol", Decimal("0.35"), version="1.0.0")
    assert registry.get_kappa("HKEX_7709", "high_vol", version="1.0.0") == Decimal("0.35")


def test_kappa_is_isolated_per_fund_regime_and_version() -> None:
    registry = KappaRegistry()
    registry.set_kappa("FUND_A", "normal", Decimal("0.2"), version="1.0.0")
    registry.set_kappa("FUND_A", "high_vol", Decimal("0.5"), version="1.0.0")
    registry.set_kappa("FUND_B", "normal", Decimal("0.1"), version="1.0.0")
    registry.set_kappa("FUND_A", "normal", Decimal("0.9"), version="2.0.0")

    assert registry.get_kappa("FUND_A", "normal", version="1.0.0") == Decimal("0.2")
    assert registry.get_kappa("FUND_A", "high_vol", version="1.0.0") == Decimal("0.5")
    assert registry.get_kappa("FUND_B", "normal", version="1.0.0") == Decimal("0.1")
    assert registry.get_kappa("FUND_A", "normal", version="2.0.0") == Decimal("0.9")
