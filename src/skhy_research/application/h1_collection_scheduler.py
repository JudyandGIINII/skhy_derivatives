"""H1 shared raw collector를 거래일마다 장중 자동 실행하는 스케줄러.

(a) KRX 캘린더로 거래일 판정 후 14:59:50 KST 시작 ~ 15:30:10 종료로 수집을 실행하는 스케줄 모듈.
휴장일 skip, 장애·재시작 복구, 중복 방지, 조회전용 키만 사용, 실주문 endpoint 미조립.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import zoneinfo
from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any

import httpx
import websockets

from skhy_research.adapters.providers.kis.h1_websocket import (
    H1_SHARED_CAPTURE_SYMBOL,
    build_h1_subscription_messages,
)
from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.application.h1_shared_raw_collector import (
    H1SharedRawCollector,
    build_kis_h1_stream_catalog,
)
from skhy_research.domain.enums import Venue
from skhy_research.ports.secrets import SecretProvider

logger = logging.getLogger("skhy_research.application.h1_collection_scheduler")

KST = zoneinfo.ZoneInfo("Asia/Seoul")

H1_COLLECTION_START_TIME = time(14, 59, 50)
H1_COLLECTION_END_TIME = time(15, 30, 10)


class H1CollectionScheduler:
    def __init__(
        self,
        *,
        secret_provider: SecretProvider,
        calendar_resolver: CalendarResolver,
        recorder: Any,
        environment: str = "vps",
        loop_interval_seconds: float = 1.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._secret_provider = secret_provider
        self._calendar_resolver = calendar_resolver
        self._recorder = recorder
        self._environment = environment
        self._loop_interval_seconds = loop_interval_seconds
        self._clock = clock or (lambda: datetime.now(KST))
        self._completed_dates: set[date] = set()
        self._stop_event = asyncio.Event()

    async def get_websocket_approval_key(self) -> str:
        """조회전용 키를 이용해 KIS WebSocket Approval Key를 획득한다."""
        app_key = self._secret_provider.get_secret("KIS_APP_KEY")
        app_secret = self._secret_provider.get_secret("KIS_APP_SECRET")
        if not app_key or not app_secret:
            raise ValueError("KIS_APP_KEY 및 KIS_APP_SECRET 환경변수가 필요합니다.")

        base_url = (
            "https://openapivts.koreainvestment.com:29443"
            if self._environment == "vps"
            else "https://openapi.koreainvestment.com:9443"
        )
        url = f"{base_url}/oauth2/Approval"
        
        headers = {"content-type": "application/json; charset=UTF-8"}
        payload = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": app_secret,
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                raise RuntimeError(
                    f"WebSocket approval key 발급 실패: {response.status_code} {response.text}"
                )
            data = response.json()
            approval_key = data.get("approval_key")
            if not approval_key:
                raise RuntimeError(f"응답에 approval_key가 없습니다: {data}")
            return approval_key

    def _get_websocket_url(self) -> str:
        if self._environment == "vps":
            return "ws://ops.koreainvestment.com:31000"
        return "ws://ops.koreainvestment.com:21000"

    def is_within_window(self, dt: datetime) -> bool:
        """주어진 일시가 수집 윈도우 시간대에 속하는지 확인한다."""
        current_time = dt.time()
        return H1_COLLECTION_START_TIME <= current_time <= H1_COLLECTION_END_TIME

    async def run(self) -> None:
        """스케줄러의 메인 루프. 무한히 반복하며 스케줄을 처리한다."""
        logger.info("H1 Collection Scheduler 시작 (env=%s)", self._environment)
        self._stop_event.clear()
        
        while not self._stop_event.is_set():
            try:
                now = self._clock()
                today = now.date()

                is_trading = self._calendar_resolver.is_trading_day(Venue.KRX, today)
                
                if is_trading and today not in self._completed_dates:  # noqa: SIM102
                    if self.is_within_window(now):
                        logger.info("수집 시간 윈도우 감지. 수집을 실행합니다. (trading_date=%s)", today)
                        collection_run_id = f"collect-{today.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
                        
                        try:
                            # KST 기준으로 오늘 종료 시각 계산
                            end_dt = datetime.combine(today, H1_COLLECTION_END_TIME, tzinfo=KST)
                            remaining_seconds = (end_dt - now).total_seconds()
                            if remaining_seconds > 0:
                                await self._execute_collection(today, collection_run_id, remaining_seconds)
                                self._completed_dates.add(today)
                                logger.info("거래일 %s 수집 완료 기록", today)
                        except Exception as e:
                            logger.error("수집 실행 중 오류 발생: %s. 다음 루프에서 재시도합니다.", e, exc_info=True)
                
                if len(self._completed_dates) > 10:
                    sorted_dates = sorted(self._completed_dates)
                    self._completed_dates = set(sorted_dates[-5:])
                    
            except Exception as e:
                logger.error("스케줄러 루프 오류: %s", e, exc_info=True)
                
            try:
                await asyncio.sleep(self._loop_interval_seconds)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        self._stop_event.set()

    async def _execute_collection(self, trading_date: date, collection_run_id: str, duration_seconds: float) -> None:
        logger.info(
            "실시간 수집 실행: date=%s, run_id=%s, duration=%.1fs",
            trading_date,
            collection_run_id,
            duration_seconds,
        )
        
        approval_key = await self.get_websocket_approval_key()
        ws_url = self._get_websocket_url()
        
        last_verified_at_utc = int(self._clock().timestamp() * 1_000_000_000)
        provider_catalog = build_kis_h1_stream_catalog(last_verified_at_utc=last_verified_at_utc)
        
        collector = H1SharedRawCollector(
            recorder=self._recorder,
            provider_catalog=provider_catalog,
            trading_date=trading_date,
            collection_run_id=collection_run_id,
        )
        
        subscription_msgs = build_h1_subscription_messages(approval_key, symbol=H1_SHARED_CAPTURE_SYMBOL)
        
        logger.info("KIS WebSocket 연결 시도: %s", ws_url)
        async with websockets.connect(ws_url) as ws:
            logger.info("KIS WebSocket 연결 완료. 구독 요청 전송.")
            for msg in subscription_msgs:
                await ws.send(msg)
                
            start_time = asyncio.get_event_loop().time()
            
            while not self._stop_event.is_set():
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= duration_seconds:
                    logger.info("수집 시간 만료(duration=%.1fs). 웹소켓 종료합니다.", duration_seconds)
                    break
                    
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("웹소켓 연결이 예기치 않게 종료되었습니다.")
                    break
                
                if isinstance(message, str):  # noqa: SIM102
                    if message.startswith("0|") or message.startswith("1|"):
                        try:
                            received_time_utc = int(self._clock().timestamp() * 1_000_000_000)
                            collector.store_frame(message, received_time_utc=received_time_utc)
                        except Exception as e:
                            logger.error("메시지 저장 중 오류 발생: %s (msg: %s)", e, message)
