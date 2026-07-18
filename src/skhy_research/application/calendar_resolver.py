"""HolidayProvider(포트)와 domain 세션 스케줄을 결합해 거래일·세션을 판정한다 (FR-06)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from skhy_research.domain.calendar import (
    VENUE_SESSION_SCHEDULE,
    is_weekend,
    local_datetime_to_utc_nanos,
    utc_nanos_to_local_datetime,
)
from skhy_research.domain.enums import Session, Venue
from skhy_research.ports.calendar import HolidayProvider


@dataclass(frozen=True)
class SessionWindowUtc:
    session: Session
    start_utc: int
    end_utc: int


class CalendarResolver:
    def __init__(self, holiday_provider: HolidayProvider) -> None:
        self._holidays = holiday_provider

    def is_trading_day(self, venue: Venue, local_date: date) -> bool:
        if is_weekend(local_date):
            return False
        return not self._holidays.is_holiday(venue, local_date)

    def session_windows_utc(self, venue: Venue, local_date: date) -> list[SessionWindowUtc]:
        if not self.is_trading_day(venue, local_date):
            return []
        windows: list[SessionWindowUtc] = []
        for window in VENUE_SESSION_SCHEDULE[venue]:
            start_utc = local_datetime_to_utc_nanos(local_date, window.start, venue)
            end_utc = local_datetime_to_utc_nanos(local_date, window.end, venue)
            windows.append(SessionWindowUtc(window.session, start_utc, end_utc))
        return windows

    def session_at(self, venue: Venue, event_time_utc: int) -> Session | None:
        """해당 시각의 세션을 반환한다. 휴장·세션 시간 밖이면 None(CLOSED로 취급)."""
        local_dt = utc_nanos_to_local_datetime(event_time_utc, venue)
        local_date = local_dt.date()
        if not self.is_trading_day(venue, local_date):
            return None
        local_time = local_dt.time()
        for window in VENUE_SESSION_SCHEDULE[venue]:
            if window.start <= local_time < window.end:
                return window.session
        return None
