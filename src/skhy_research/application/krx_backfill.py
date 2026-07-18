"""KRX 공식 일별 데이터 백필 오케스트레이션 (P1-01, FR-02~06).

실제 백필은 G-04(레버리지 상품 universe)·G-06(데이터 이용조건) 게이트 해소와
실제 KRX 조회 전용 키가 필요하다. Markdown gate 문서는 사람용 검토 기록이며,
호출자는 PostgreSQL의 최신 결정을 `load_gate_registry()`로 검증·로드한 registry를
주입해야 한다. 이 모듈은 `HistoricalDataProvider` 포트로만 동작하므로 fixture
구현과 실제 어댑터가 동일한 코드 경로를 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.application.gate_registry import GateRegistry
from skhy_research.application.provider_registry import ProviderRegistry
from skhy_research.application.trading_day_coverage import (
    CoverageReport,
    verify_trading_day_coverage,
)
from skhy_research.domain.enums import AdjustmentStatus, Venue
from skhy_research.domain.market import Bar


@dataclass(frozen=True)
class ReconciliationMismatch:
    bar_close_time_utc: int
    primary_close: Decimal
    secondary_close: Decimal
    diff_pct: Decimal


@dataclass(frozen=True)
class BackfillResult:
    bars: tuple[Bar, ...]
    coverage: CoverageReport
    reconciliation_mismatches: tuple[ReconciliationMismatch, ...]


class BackfillGateBlockedError(RuntimeError):
    """필수 데이터·universe gate가 해소되지 않아 백필을 시작할 수 없음."""


def backfill_daily_bars(
    registry: ProviderRegistry,
    calendar_resolver: CalendarResolver,
    venue: Venue,
    primary_provider_name: str,
    instrument_id: str,
    start: date,
    end: date,
    start_utc: int,
    end_utc: int,
    *,
    gate_registry: GateRegistry,
    gate_as_of_utc: int,
    secondary_provider_name: str | None = None,
    close_tolerance_pct: Decimal = Decimal("0.5"),
) -> BackfillResult:
    blocked_gates = [
        gate_id for gate_id in ("G-04", "G-06") if gate_registry.blocks(gate_id, gate_as_of_utc)
    ]
    if blocked_gates:
        raise BackfillGateBlockedError(
            f"KRX 백필 차단: CONFIRMED가 아닌 필수 gate={blocked_gates}"
        )

    primary = registry.get_historical_data(primary_provider_name)
    bars = primary.get_bars(instrument_id, "1d", start_utc, end_utc, AdjustmentStatus.RAW)
    coverage = verify_trading_day_coverage(calendar_resolver, venue, start, end, bars)

    mismatches: list[ReconciliationMismatch] = []
    if secondary_provider_name is not None:
        secondary = registry.get_historical_data(secondary_provider_name)
        secondary_bars = secondary.get_bars(
            instrument_id, "1d", start_utc, end_utc, AdjustmentStatus.RAW
        )
        secondary_by_close_time = {b.bar_close_time_utc: b for b in secondary_bars}
        for bar in bars:
            match = secondary_by_close_time.get(bar.bar_close_time_utc)
            if match is None or bar.close == 0:
                continue
            diff_pct = abs(bar.close - match.close) / bar.close * Decimal("100")
            if diff_pct > close_tolerance_pct:
                mismatches.append(
                    ReconciliationMismatch(bar.bar_close_time_utc, bar.close, match.close, diff_pct)
                )

    return BackfillResult(bars=tuple(bars), coverage=coverage, reconciliation_mismatches=tuple(mismatches))
