"""REST snapshot 공급자의 시각·세션 정규화 공통 함수."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from skhy_research.domain.calendar import (
    VENUE_SESSION_SCHEDULE,
    VENUE_TIMEZONE,
    is_weekend,
    local_datetime_to_utc_nanos,
    utc_nanos_to_local_datetime,
)
from skhy_research.domain.enums import Session, Venue


def parse_provider_iso_timestamp(value: object) -> int:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("공급자 timestamp가 없다")
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        raise ValueError("공급자 timestamp에 timezone이 없다")
    utc_value = parsed.astimezone(UTC)
    return int(utc_value.timestamp() * 1_000_000_000)


def parse_kis_date_time(date_value: object, time_value: object) -> int:
    if not isinstance(date_value, str) or len(date_value) != 8 or not date_value.isdigit():
        raise ValueError("KIS 영업일자는 YYYYMMDD여야 한다")
    trading_date = date(int(date_value[:4]), int(date_value[4:6]), int(date_value[6:]))
    return combine_kis_time(trading_date, time_value)


def combine_kis_time(trading_date: date, value: object) -> int:
    if not isinstance(value, str) or len(value) != 6 or not value.isdigit():
        raise ValueError("KIS 영업시간은 HHMMSS여야 한다")
    local_time = time(int(value[:2]), int(value[2:4]), int(value[4:]))
    return local_datetime_to_utc_nanos(trading_date, local_time, Venue.KRX)


def provider_trading_date(event_time_utc: int, venue: Venue = Venue.KRX) -> date:
    return utc_nanos_to_local_datetime(event_time_utc, venue).date()


def session_at(event_time_utc: int, venue: Venue) -> Session:
    local = utc_nanos_to_local_datetime(event_time_utc, venue)
    if is_weekend(local.date()):
        return Session.CLOSED
    for window in VENUE_SESSION_SCHEDULE.get(venue, ()):
        if window.start <= local.timetz().replace(tzinfo=None) < window.end:
            return window.session
    return Session.CLOSED


def requested_time_kst(requested_as_of_utc: int) -> str:
    local = datetime.fromtimestamp(
        requested_as_of_utc / 1_000_000_000,
        tz=UTC,
    ).astimezone(VENUE_TIMEZONE[Venue.KRX])
    return local.strftime("%H%M%S")
