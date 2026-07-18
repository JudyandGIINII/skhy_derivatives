"""휴장일 조회 포트. 실제 구현(KRX/공식 캘린더)은 P0-07/Phase 1에서 등록한다."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from skhy_research.domain.enums import Venue


class HolidayProvider(Protocol):
    def is_holiday(self, venue: Venue, local_date: date) -> bool: ...
