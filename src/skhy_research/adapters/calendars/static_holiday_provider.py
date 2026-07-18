"""테스트·개발용 고정 휴장일 목록.

운영 정확도는 P0-07의 공식 참조데이터 어댑터(KRX/Nasdaq/HKEX, G-04 인접 항목)로
교체·검증해야 한다. 이 구현은 fixture 계약 테스트와 초기 개발 편의용이다.
"""

from __future__ import annotations

from datetime import date

from skhy_research.domain.enums import Venue
from skhy_research.ports.calendar import HolidayProvider


class StaticHolidayProvider(HolidayProvider):
    def __init__(self, holidays_by_venue: dict[Venue, set[date]] | None = None) -> None:
        self._holidays: dict[Venue, set[date]] = holidays_by_venue or {}

    def is_holiday(self, venue: Venue, local_date: date) -> bool:
        return local_date in self._holidays.get(venue, set())
