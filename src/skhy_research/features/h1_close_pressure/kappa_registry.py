"""상품·국면(regime)별 kappa(실제 전이계수) 저장소 (PRD 9.1).

kappa는 학습 구간에서만 추정하며, 합성상품(SWAP)에 kappa=1을 가정하지 않는다.
이 저장소는 명시적으로 설정되지 않은 (fund_id, regime, version)에 대해
어떤 기본값도 반환하지 않는다 — 호출자가 `None`을 받으면 축소모델로 처리해야 한다.
"""

from __future__ import annotations

from decimal import Decimal

_KappaKey = tuple[str, str, str]  # (fund_id, regime, strategy_version)


class KappaRegistry:
    def __init__(self) -> None:
        self._kappa: dict[_KappaKey, Decimal] = {}

    def set_kappa(self, fund_id: str, regime: str, kappa: Decimal, *, version: str) -> None:
        self._kappa[(fund_id, regime, version)] = kappa

    def get_kappa(self, fund_id: str, regime: str, *, version: str) -> Decimal | None:
        return self._kappa.get((fund_id, regime, version))
