"""H1 Collection Scheduler 단위 테스트."""

from __future__ import annotations

import asyncio
import zoneinfo
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skhy_research.adapters.calendars.static_holiday_provider import StaticHolidayProvider
from skhy_research.application.calendar_resolver import CalendarResolver
from skhy_research.application.h1_collection_scheduler import H1CollectionScheduler
from skhy_research.domain.enums import Venue
from tests._h1_shared_stream_support import MemoryRawRecorder, load_h1_shared_fixture

KST = zoneinfo.ZoneInfo("Asia/Seoul")


class MockSecretProvider:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, name: str) -> str | None:
        return self._secrets.get(name)


@pytest.fixture
def calendar_resolver() -> CalendarResolver:
    # 2026-07-20 (월)은 평일
    # 2026-07-19 (일)은 주말
    # 2026-07-22 (수)는 휴장일로 지정
    holiday_provider = StaticHolidayProvider({
        Venue.KRX: {date(2026, 7, 22)}
    })
    return CalendarResolver(holiday_provider)


@pytest.fixture
def secret_provider() -> MockSecretProvider:
    return MockSecretProvider({
        "KIS_APP_KEY": "mock_app_key",
        "KIS_APP_SECRET": "mock_app_secret",
    })


@pytest.fixture
def recorder() -> MemoryRawRecorder:
    return MemoryRawRecorder()


def test_is_within_window(secret_provider, calendar_resolver, recorder) -> None:
    scheduler = H1CollectionScheduler(
        secret_provider=secret_provider,
        calendar_resolver=calendar_resolver,
        recorder=recorder,
        loop_interval_seconds=0.1,
    )

    # 14:59:49 KST (윈도우 전)
    dt_before = datetime(2026, 7, 20, 14, 59, 49, tzinfo=KST)
    assert not scheduler.is_within_window(dt_before)

    # 14:59:50 KST (윈도우 경계 시작)
    dt_start = datetime(2026, 7, 20, 14, 59, 50, tzinfo=KST)
    assert scheduler.is_within_window(dt_start)

    # 15:15:00 KST (윈도우 안)
    dt_inside = datetime(2026, 7, 20, 15, 15, 0, tzinfo=KST)
    assert scheduler.is_within_window(dt_inside)

    # 15:30:10 KST (윈도우 경계 종료)
    dt_end = datetime(2026, 7, 20, 15, 30, 10, tzinfo=KST)
    assert scheduler.is_within_window(dt_end)

    # 15:30:11 KST (윈도우 후)
    dt_after = datetime(2026, 7, 20, 15, 30, 11, tzinfo=KST)
    assert not scheduler.is_within_window(dt_after)


def test_skip_non_trading_day(secret_provider, calendar_resolver, recorder) -> None:
    async def _run() -> None:
        # 일요일 15:00:00 KST
        sunday_dt = datetime(2026, 7, 19, 15, 0, 0, tzinfo=KST)
        scheduler = H1CollectionScheduler(
            secret_provider=secret_provider,
            calendar_resolver=calendar_resolver,
            recorder=recorder,
            clock=lambda: sunday_dt,
            loop_interval_seconds=0.01,
        )

        with patch.object(scheduler, "_execute_collection", new_callable=AsyncMock) as mock_execute:
            # 루프를 한 번만 수행 후 멈추게 stop 설정
            async def stop_after_one_loop():
                await asyncio.sleep(0.02)
                scheduler.stop()

            asyncio.create_task(stop_after_one_loop())
            await scheduler.run()

            mock_execute.assert_not_called()

        # 휴장일(수요일) 15:00:00 KST
        holiday_dt = datetime(2026, 7, 22, 15, 0, 0, tzinfo=KST)
        scheduler_holiday = H1CollectionScheduler(
            secret_provider=secret_provider,
            calendar_resolver=calendar_resolver,
            recorder=recorder,
            clock=lambda: holiday_dt,
            loop_interval_seconds=0.01,
        )

        with patch.object(scheduler_holiday, "_execute_collection", new_callable=AsyncMock) as mock_execute:
            async def stop_after_one_loop_h():
                await asyncio.sleep(0.02)
                scheduler_holiday.stop()

            asyncio.create_task(stop_after_one_loop_h())
            await scheduler_holiday.run()

            mock_execute.assert_not_called()

    asyncio.run(_run())


def test_trigger_during_window(secret_provider, calendar_resolver, recorder) -> None:
    async def _run() -> None:
        # 월요일 (거래일) 15:00:00 KST -> 윈도우 내 기동
        trading_dt = datetime(2026, 7, 20, 15, 0, 0, tzinfo=KST)
        scheduler = H1CollectionScheduler(
            secret_provider=secret_provider,
            calendar_resolver=calendar_resolver,
            recorder=recorder,
            clock=lambda: trading_dt,
            loop_interval_seconds=0.01,
        )

        with patch.object(scheduler, "_execute_collection", new_callable=AsyncMock) as mock_execute:
            async def stop_after_one_loop():
                await asyncio.sleep(0.02)
                scheduler.stop()

            asyncio.create_task(stop_after_one_loop())
            await scheduler.run()

            # 15:00:00 KST 부터 15:30:10 KST 까지는 1810초 남음
            mock_execute.assert_called_once()
            args = mock_execute.call_args[0]
            assert args[0] == date(2026, 7, 20)
            assert args[1].startswith("collect-20260720-")
            assert abs(args[2] - 1810.0) < 1.0

    asyncio.run(_run())


def test_execute_collection_with_mock_websockets(secret_provider, calendar_resolver, recorder) -> None:
    async def _run() -> None:
        # 2026-07-20 월요일 수집 시나리오
        trading_dt = datetime(2026, 7, 20, 15, 0, 0, tzinfo=KST)
        scheduler = H1CollectionScheduler(
            secret_provider=secret_provider,
            calendar_resolver=calendar_resolver,
            recorder=recorder,
            clock=lambda: trading_dt,
        )

        # 1. Mock HTTP Response for Approval Key
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"approval_key": "mock_approval_key_123"}
        
        # 2. Mock Websocket connection and packets
        mock_ws = AsyncMock()
        
        # fixture 데이터를 로드해서 웹소켓에서 내려줄 KIS 프레임 조립
        _, packets = load_h1_shared_fixture()
        assert len(packets) > 0
        
        # 웹소켓에서 패킷의 record_frame을 반환하도록 설정
        frames_to_send = [p.record_frame for p in packets]
        
        iterator = iter(frames_to_send)
        async def mock_recv():
            try:
                return next(iterator)
            except StopIteration:
                await asyncio.sleep(10)
                raise TimeoutError() from None
        mock_ws.recv.side_effect = mock_recv

        mock_connect_context = MagicMock()
        mock_connect_context.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect_context.__aexit__ = AsyncMock()

        with patch("httpx.AsyncClient.post", return_value=mock_response), \
             patch("websockets.connect", return_value=mock_connect_context) as mock_connect:

            # 0.1초 동안만 수집을 수행하도록 duration 설정
            await scheduler._execute_collection(
                trading_date=date(2026, 7, 20),
                collection_run_id="test-run-123",
                duration_seconds=0.1
            )

            mock_connect.assert_called_once_with("ws://ops.koreainvestment.com:31000")
            assert mock_ws.send.call_count == 5  # 5개 토픽 구독 메시지 전송

            # MemoryRawRecorder에 패킷들이 정상적으로 저장되었는지 확인
            assert len(recorder.payloads) > 0

    asyncio.run(_run())
