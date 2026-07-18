"""거래일 커버리지 검증 (P1-01 완료조건: 최소 120 KRX 거래일)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.domain.calendar import utc_nanos_to_local_datetime
from skhy_research.domain.enums import Venue
from skhy_research.domain.market import Bar


@dataclass(frozen=True)
class CoverageReport:
    expected_trading_days: int
    covered_trading_days: int
    missing_dates: tuple[date, ...]

    @property
    def is_complete(self) -> bool:
        return not self.missing_dates

    def meets_minimum(self, minimum_trading_days: int) -> bool:
        return self.is_complete and self.covered_trading_days >= minimum_trading_days


def expected_trading_days(
    resolver: CalendarResolver, venue: Venue, start: date, end: date
) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        if resolver.is_trading_day(venue, cur):
            days.append(cur)
        cur += timedelta(days=1)
    return days


def verify_trading_day_coverage(
    resolver: CalendarResolver, venue: Venue, start: date, end: date, bars: list[Bar]
) -> CoverageReport:
    expected = expected_trading_days(resolver, venue, start, end)
    covered_dates = {utc_nanos_to_local_datetime(b.bar_close_time_utc, venue).date() for b in bars}
    missing = tuple(d for d in expected if d not in covered_dates)
    return CoverageReport(
        expected_trading_days=len(expected),
        covered_trading_days=len(expected) - len(missing),
        missing_dates=missing,
    )
