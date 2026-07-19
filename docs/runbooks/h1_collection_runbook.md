# H1 Shared Raw Collector 자동 실행 런북 (H1 Collection Runbook)

이 런북은 KIS 실시간 시세 raw 수집기(`src/skhy_research/application/h1_shared_raw_collector.py`)를 거래일마다 장중(14:59:50 KST ~ 15:30:10 KST)에 자동으로 실행하고 예외 상황에서 복구하기 위한 스케줄링 가이드라인을 제공합니다.

---

## 1. 개요 및 안전 조치

- **수집 대상**: SK하이닉스(`000660`)의 실시간 호가/체결/프로그램 매매 raw 패킷.
- **수집 시간**: 거래일 **14:59:50 KST ~ 15:30:10 KST** (총 30분 20초).
- **조회 전용(Read-only) 격리**:
  - 본 스케줄러는 KIS 시세 수집용 웹소켓 연결만을 수립하며, 실주문 API 엔드포인트나 계좌 관련 포트는 조립하지 않고 원천 배제합니다.
  - API Key는 조회 권한만 부여된 키를 사용하도록 권장합니다.
- **오류 및 재시작 복구**:
  - 수집 중 비정상 종료(네트워크 단절, 프로세스 킬 등)가 발생해 재시작하더라도, 현재 시각이 수집 윈도우(14:59:50 ~ 15:30:10 KST) 내에 있다면 **즉시 웹소켓 재접속 후 남은 시간 동안 수집을 재개**합니다.
  - 하루 한 번만 완료되도록 일별 완료 내역(`completed_dates`)을 추적하여 중복 수집을 방지합니다.

---

## 2. 방법 A: macOS launchd 데몬 등록 (권장)

macOS에 내장된 서비스 관리자 `launchd`를 사용하여 스케줄러 스크립트(`src/skhy_research/application/run_scheduler.py`)를 백그라운드 상주 서비스로 실행하는 방법입니다. 프로세스가 다운되면 `launchd`가 자동으로 재시작하므로 장애 복구에 적합합니다.

### A.1. launchd Plist 파일 작성
`~/Library/LaunchAgents/com.skhy.h1.collector.plist` 파일을 생성하고 아래 내용을 입력합니다.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.skhy.h1.collector</string>

    <key>ProgramArguments</key>
    <array>
        <!-- 프로젝트 가상환경 내 python 실행 경로 -->
        <string>/Users/hipme_mini/Documents/ai_app/skhy_파생이용/.venv/bin/python</string>
        <!-- 스케줄러 실행 엔트리포인트 스크립트 경로 -->
        <string>/Users/hipme_mini/Documents/ai_app/skhy_파생이용/src/skhy_research/application/run_scheduler.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/hipme_mini/Documents/ai_app/skhy_파생이용</string>

    <!-- 서비스 자동 재기동 설정 (장애/비정상 종료 대비) -->
    <key>KeepAlive</key>
    <true/>

    <key>RunAtLoad</key>
    <true/>

    <!-- 환경변수 주입 (macOS Keychain을 사용하지 않고 env로 주입할 경우) -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>SKHY_ENV</key>
        <string>local</string>
        <key>KIS_ENV</key>
        <string>vps</string> <!-- 실전 환경 승격 시 prod 로 변경 -->
        <key>SKHY_SECRET_BACKEND</key>
        <string>env</string>
        <key>KIS_APP_KEY</key>
        <string>YOUR_KIS_APP_KEY</string>
        <key>KIS_APP_SECRET</key>
        <string>YOUR_KIS_APP_SECRET</string>
        <key>SKHY_DATABASE_URL</key>
        <string>postgresql://localhost/skhy_derivatives</string>
    </dict>

    <!-- 표준 출력 및 에러 로깅 설정 -->
    <key>StandardOutPath</key>
    <string>/Users/hipme_mini/Documents/ai_app/skhy_파생이용/var/log/h1_scheduler_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/hipme_mini/Documents/ai_app/skhy_파생이용/var/log/h1_scheduler_stderr.log</string>
</dict>
</plist>
```

> [!NOTE]
> `SKHY_SECRET_BACKEND`를 `keychain`으로 설정한 경우, macOS Keychain에 `skhy-research` 서비스명으로 `KIS_APP_KEY`와 `KIS_APP_SECRET` 패스워드를 등록하여 plist 노출 없이 안전하게 비밀키를 관리할 수 있습니다.

### A.2. launchd 서비스 관리 명령어
서비스 등록 및 시작:
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.skhy.h1.collector.plist
```

서비스 중지 및 해제:
```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.skhy.h1.collector.plist
```

서비스 현재 상태 확인:
```bash
launchctl list | grep com.skhy.h1.collector
```

---

## 3. 방법 B: APScheduler 기반 예약 설정

만약 외부 스케줄 정기 루프가 아니라, Python 애플리케이션 자체 내에서 `APScheduler`를 이용해 정확히 14:59:50 KST에 태스크를 기동시키고 싶다면 아래 예제 스크립트를 참조하여 구성할 수 있습니다.

```python
"""APScheduler를 이용한 H1 수집 예약 태스크 구동 예시."""

import asyncio
from datetime import date
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from skhy_research.application.h1_collection_scheduler import H1CollectionScheduler

# 스케줄러 의존성 인스턴스 (secret_provider, calendar_resolver, recorder)가 생성되었다고 가정
async def scheduled_collection_job(scheduler_inst: H1CollectionScheduler):
    today = date.today()
    
    # 영업일 여부 판정
    is_trading = scheduler_inst._calendar_resolver.is_trading_day(
        venue=Venue.KRX, 
        local_date=today
    )
    if not is_trading:
        print(f"{today}는 휴장일입니다. 수집을 스킵합니다.")
        return
        
    print(f"{today} 장종료 시세 수집을 시작합니다.")
    collection_run_id = f"collect-ap-{today.strftime('%Y%m%d')}"
    
    # 14:59:50 KST ~ 15:30:10 KST 까지의 1820초 동안 수집 실행
    await scheduler_inst._execute_collection(
        trading_date=today,
        collection_run_id=collection_run_id,
        duration_seconds=1820.0
    )

async def main():
    scheduler_inst = ... # 초기화된 H1CollectionScheduler 인스턴스
    
    # AsyncIOScheduler 생성
    apsched = AsyncIOScheduler(timezone="Asia/Seoul")
    
    # 매주 월요일~금요일 14:59:50 KST 에 실행되도록 CronTrigger 설정
    trigger = CronTrigger(day_of_week="mon-fri", hour=14, minute=59, second=50, timezone="Asia/Seoul")
    
    apsched.add_job(
        scheduled_collection_job,
        trigger=trigger,
        args=[scheduler_inst],
        id="h1_collection_job",
        replace_existing=True
    )
    
    apsched.start()
    print("APScheduler 기반 H1 수집 예약 완료 (월-금 14:59:50 KST)")
    
    # 스케줄러 루프를 대기 상태로 유지
    while True:
        await asyncio.sleep(1000)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 4. 로깅 및 모니터링 가이드

1. **로깅 파일**:
   - `var/log/h1_scheduler_stdout.log` 및 `var/log/h1_scheduler_stderr.log`를 모니터링합니다.
2. **정상 수집 완료 시 로그 로그 패턴**:
   ```
   2026-07-20 14:59:50,001 [INFO] skhy_research.application.h1_collection_scheduler: 수집 시간 윈도우 감지. 수집을 실행합니다. (trading_date=2026-07-20)
   2026-07-20 14:59:50,050 [INFO] skhy_research.application.h1_collection_scheduler: 실시간 수집 실행: date=2026-07-20, run_id=collect-20260720-3b91a82f, duration=1820.0s
   2026-07-20 14:59:50,450 [INFO] skhy_research.application.h1_collection_scheduler: KIS WebSocket 연결 시도: ws://ops.koreainvestment.com:31000
   2026-07-20 14:59:51,100 [INFO] skhy_research.application.h1_collection_scheduler: KIS WebSocket 연결 완료. 구독 요청 전송.
   ... (수집 중) ...
   2026-07-20 15:30:10,001 [INFO] skhy_research.application.h1_collection_scheduler: 수집 시간 만료(duration=1820.0s). 웹소켓 종료합니다.
   2026-07-20 15:30:10,010 [INFO] skhy_research.application.h1_collection_scheduler: 거래일 2026-07-20 수집 완료 기록
   ```
3. **네트워크 장애 및 끊김 발생 시**:
   - KIS 웹소켓 서버에서 연결을 예기치 않게 종료하는 경우 `[WARNING] 웹소켓 연결이 예기치 않게 종료되었습니다.` 로그를 남기고 종료됩니다.
   - `launchd`가 데몬을 즉시 재기동(KeepAlive=True)시키며, 재시작된 스케줄러는 현재 시각이 윈도우 내(15:30:10 KST 전)인지 확인하고 **즉시 재접속을 수행해 남은 시간 동안 수집을 완료**합니다.
