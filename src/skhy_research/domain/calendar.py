"""거래소별 로컬 세션 스케줄과 zoneinfo 기반 로컬→UTC 변환 (FR-06, PRD 9.3).

세션 시각은 거래소 로컬 시간대(zoneinfo)로 정의한다. DST는 별도 규칙표 없이
`zoneinfo`의 IANA 시간대 데이터가 자동으로 반영하므로, "거래시간을 고정 KST
문자열로 저장하지 않고 거래소 캘린더와 DST로 계산한다"(PRD 9.3)는 요구를
연도에 무관하게 만족한다.

NXT 프리·애프터마켓의 정확한 경계와 국내 단일종목 상품 세션은 P0-07에서
공식 자료(Nextrade/KRX)로 재확인해야 한다 (G-04 인접 항목). 여기 값은
`implementation_plan.md` 10장 참고자료 기준 최선 추정치다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from skhy_research.domain.enums import Session, Venue

VENUE_TIMEZONE: dict[Venue, ZoneInfo] = {
    Venue.KRX: ZoneInfo("Asia/Seoul"),
    Venue.NXT: ZoneInfo("Asia/Seoul"),
    Venue.NASDAQ: ZoneInfo("America/New_York"),
    Venue.HKEX: ZoneInfo("Asia/Hong_Kong"),
}


@dataclass(frozen=True)
class LocalSessionWindow:
    session: Session
    start: time
    end: time  # 배타적 상한(해당 시각 자체는 다음 세션에 속한다)


VENUE_SESSION_SCHEDULE: dict[Venue, tuple[LocalSessionWindow, ...]] = {
    Venue.KRX: (
        LocalSessionWindow(Session.REGULAR, time(9, 0), time(15, 20)),
        LocalSessionWindow(Session.CLOSE_AUCTION, time(15, 20), time(15, 30)),
    ),
    Venue.NXT: (
        LocalSessionWindow(Session.PRE, time(8, 0), time(8, 50)),
        LocalSessionWindow(Session.REGULAR, time(9, 0), time(15, 20)),
        LocalSessionWindow(Session.CLOSE_AUCTION, time(15, 20), time(15, 30)),
        LocalSessionWindow(Session.AFTER, time(15, 30), time(20, 0)),
    ),
    Venue.NASDAQ: (
        LocalSessionWindow(Session.PRE, time(4, 0), time(9, 30)),
        LocalSessionWindow(Session.REGULAR, time(9, 30), time(16, 0)),
        LocalSessionWindow(Session.AFTER, time(16, 0), time(20, 0)),
    ),
    Venue.HKEX: (
        LocalSessionWindow(Session.REGULAR, time(9, 30), time(12, 0)),
        LocalSessionWindow(Session.REGULAR, time(13, 0), time(16, 0)),
    ),
}


def is_weekend(local_date: date) -> bool:
    return local_date.weekday() >= 5  # 5=토, 6=일


def local_datetime_to_utc_nanos(local_date: date, local_time: time, venue: Venue) -> int:
    tz = VENUE_TIMEZONE[venue]
    local_dt = datetime.combine(local_date, local_time, tzinfo=tz)
    utc_dt = local_dt.astimezone(UTC)
    return int(utc_dt.timestamp() * 1_000_000_000)


def utc_nanos_to_local_datetime(event_time_utc: int, venue: Venue) -> datetime:
    utc_dt = datetime.fromtimestamp(event_time_utc / 1_000_000_000, tz=UTC)
    return utc_dt.astimezone(VENUE_TIMEZONE[venue])
