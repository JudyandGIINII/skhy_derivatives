"""H1 Collection Scheduler CLI 실행 진입점.

 launchd plist나 background daemon으로 실행되어 장중 수집을 자동 스케줄링한다.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import date

from dotenv import load_dotenv

from skhy_research.adapters.calendars.static_holiday_provider import StaticHolidayProvider
from skhy_research.adapters.persistence.db import build_engine
from skhy_research.adapters.persistence.raw_recorder import RawRecorder
from skhy_research.adapters.secrets.factory import build_secret_provider
from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.application.config import get_settings
from skhy_research.application.h1_collection_scheduler import H1CollectionScheduler
from skhy_research.domain.enums import Venue

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("skhy_research.application.run_scheduler")

# 2026년 KRX 공휴일 목록 (기본값)
# 주말은 calendar_resolver의 is_weekend에서 자동으로 걸러지므로 공휴일만 지정합니다.
KRX_2026_HOLIDAYS = {
    date(2026, 1, 1),    # 신정
    date(2026, 2, 16),   # 설날 연휴
    date(2026, 2, 17),   # 설날
    date(2026, 2, 18),   # 설날 연휴
    date(2026, 3, 2),    # 삼일절 대체공휴일 (3/1 일요일)
    date(2026, 5, 5),    # 어린이날
    date(2026, 5, 25),   # 석가탄신일 대체공휴일 (5/24 일요일)
    date(2026, 6, 6),    # 현충일
    date(2026, 8, 15),   # 광복절
    date(2026, 9, 24),   # 추석 연휴
    date(2026, 9, 25),   # 추석
    date(2026, 9, 26),   # 추석 연휴
    date(2026, 10, 3),   # 개천절
    date(2026, 10, 9),   # 한글날
    date(2026, 12, 25),  # 성탄절
    date(2026, 12, 31),  # 연말 휴장일
}


async def main() -> None:
    # 1. 환경변수 로딩
    load_dotenv()
    
    # 2. 설정 로딩 및 DB 엔진 빌드
    settings = get_settings()
    engine = build_engine(settings)
    
    # 3. Secret Provider 빌드
    secret_provider = build_secret_provider()
    
    # 4. 캘린더 리졸버 구성
    holiday_provider = StaticHolidayProvider({Venue.KRX: KRX_2026_HOLIDAYS})
    calendar_resolver = CalendarResolver(holiday_provider)
    
    # 5. Raw Recorder 구성 (SQLAlchemy engine & data_root)
    recorder = RawRecorder(engine, settings.data_root)
    
    # 6. 스케줄러 인스턴스 구성
    # KIS_ENV 환경변수를 읽어서 "vps" 또는 "prod" 중 설정 (기본값 "vps")
    import os
    env_name = os.environ.get("KIS_ENV", "vps")
    
    scheduler = H1CollectionScheduler(
        secret_provider=secret_provider,
        calendar_resolver=calendar_resolver,
        recorder=recorder,
        environment=env_name,
        loop_interval_seconds=5.0,  # 프로덕션에서는 5초 주기로 루프
    )
    
    # 7. Graceful Shutdown 시그널 핸들러 등록
    loop = asyncio.get_running_loop()
    
    def shutdown():
        logger.info("종료 시그널 수신. 스케줄러를 정지합니다...")
        scheduler.stop()
        
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)
        
    # 8. 스케줄러 실행
    try:
        await scheduler.run()
    except Exception as e:
        logger.critical("스케줄러 비정상 종료: %s", e, exc_info=True)
        sys.exit(1)
        
    logger.info("스케줄러가 안전하게 정지되었습니다.")


if __name__ == "__main__":
    asyncio.run(main())
