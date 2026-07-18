"""P0-05 검증: 거래일·세션 판정과 DST가 zoneinfo로 올바르게 계산되는지 확인한다."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from skhy_research.adapters.calendars.static_holiday_provider import StaticHolidayProvider
from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.application.config import load_settings
from skhy_research.domain.calendar import VENUE_SESSION_SCHEDULE, local_datetime_to_utc_nanos
from skhy_research.domain.enums import Session, Venue

_A_WEDNESDAY = date(2026, 7, 15)
_A_SATURDAY = date(2026, 7, 18)
_A_THURSDAY = date(2026, 7, 16)


def test_weekend_is_never_a_trading_day() -> None:
    resolver = CalendarResolver(StaticHolidayProvider())
    assert resolver.is_trading_day(Venue.KRX, _A_SATURDAY) is False
    assert resolver.session_windows_utc(Venue.KRX, _A_SATURDAY) == []


def test_weekday_without_holiday_is_trading_day() -> None:
    resolver = CalendarResolver(StaticHolidayProvider())
    assert resolver.is_trading_day(Venue.KRX, _A_WEDNESDAY) is True
    assert resolver.is_trading_day(Venue.NASDAQ, _A_WEDNESDAY) is True


def test_holiday_is_venue_isolated() -> None:
    provider = StaticHolidayProvider({Venue.KRX: {_A_THURSDAY}})
    resolver = CalendarResolver(provider)

    assert resolver.is_trading_day(Venue.KRX, _A_THURSDAY) is False
    assert resolver.is_trading_day(Venue.NASDAQ, _A_THURSDAY) is True  # 다른 거래소는 영향 없음


def test_session_at_classifies_krx_regular_and_close_auction() -> None:
    resolver = CalendarResolver(StaticHolidayProvider())

    regular_ts = local_datetime_to_utc_nanos(_A_WEDNESDAY, time(9, 30), Venue.KRX)
    close_auction_ts = local_datetime_to_utc_nanos(_A_WEDNESDAY, time(15, 25), Venue.KRX)
    before_open_ts = local_datetime_to_utc_nanos(_A_WEDNESDAY, time(7, 0), Venue.KRX)

    assert resolver.session_at(Venue.KRX, regular_ts) == Session.REGULAR
    assert resolver.session_at(Venue.KRX, close_auction_ts) == Session.CLOSE_AUCTION
    assert resolver.session_at(Venue.KRX, before_open_ts) is None


def test_session_at_classifies_nxt_pre_market() -> None:
    resolver = CalendarResolver(StaticHolidayProvider())
    pre_ts = local_datetime_to_utc_nanos(_A_WEDNESDAY, time(8, 20), Venue.NXT)
    assert resolver.session_at(Venue.NXT, pre_ts) == Session.PRE


def test_session_at_returns_none_on_holiday() -> None:
    provider = StaticHolidayProvider({Venue.KRX: {_A_WEDNESDAY}})
    resolver = CalendarResolver(provider)
    ts = local_datetime_to_utc_nanos(_A_WEDNESDAY, time(10, 0), Venue.KRX)
    assert resolver.session_at(Venue.KRX, ts) is None


def test_nasdaq_dst_shifts_utc_offset_by_one_hour() -> None:
    """1월(EST, UTC-5)과 7월(EDT, UTC-4) 같은 로컬 09:30이 서로 다른 UTC 시각이어야 한다.

    거래시간을 고정 오프셋으로 하드코딩하지 않고 zoneinfo가 DST를 자동 계산함을 증명한다.
    """
    winter_utc_ns = local_datetime_to_utc_nanos(date(2026, 1, 15), time(9, 30), Venue.NASDAQ)
    summer_utc_ns = local_datetime_to_utc_nanos(date(2026, 7, 15), time(9, 30), Venue.NASDAQ)

    winter_hour = datetime.fromtimestamp(winter_utc_ns / 1_000_000_000, tz=UTC).hour
    summer_hour = datetime.fromtimestamp(summer_utc_ns / 1_000_000_000, tz=UTC).hour

    assert winter_hour == 14  # EST: UTC-5
    assert summer_hour == 13  # EDT: UTC-4
    assert winter_hour != summer_hour


def test_krx_close_auction_start_matches_h1_entry_window_end_config() -> None:
    """base.yaml의 h1.entry_window_end_kst(15:20)이 KRX 종가경매 시작 시각과 일치해야 한다."""
    settings = load_settings("local")
    configured_end = time.fromisoformat(settings.h1.entry_window_end_kst)

    krx_windows = VENUE_SESSION_SCHEDULE[Venue.KRX]
    close_auction = next(w for w in krx_windows if w.session == Session.CLOSE_AUCTION)

    assert close_auction.start == configured_end
