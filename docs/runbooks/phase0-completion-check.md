# Phase 0 완료조건 점검 (`implementation_plan.md` 5.2)

`implementation_plan.md`가 정의한 Phase 0 완료조건 4가지와 현재 상태.

| # | 완료조건 | 상태 | 근거 |
| --- | --- | --- | --- |
| 1 | fixture 기반 공급자 계약·정규화·캘린더·재시작 테스트가 통과한다 | ✅ 충족 | `tests/contract/test_fixture_provider_contract.py`, `tests/unit/test_normalizer.py`, `tests/unit/test_calendar_resolver.py`, `tests/integration/test_raw_recorder.py`(재시작 catch-up) |
| 2 | 사용자가 조회 전용 키를 주입한 환경에서 KRX/KIS/Toss capability smoke가 통과하거나, 실패 capability가 명시적으로 비지원·차단 상태로 기록된다 | ✅ 충족 | 2026-07-18 macOS Keychain에 주입된 조회 전용 키로 `SKHY_SECRET_BACKEND=keychain uv run pytest -m smoke tests/e2e/test_provider_smoke.py` 3건(KRX/KIS/Toss) 통과. capability probe 실측 증거는 `docs/decisions/gates/G-02.md`(상태 `CONFIRMED`)와 `evidence/G-02-capability-probe.json`에 고정. |
| 3 | 원시 레코드 하나에서 source, 수신시각, checksum, 이용조건, 정규화 레코드까지 추적된다 | ✅ 충족 | `tests/e2e/test_phase0_completion.py::test_raw_record_is_traceable_to_normalized_record` (raw_recorder → normalizer → lineage_edge 실제 PostgreSQL round trip) |
| 4 | broker registry와 배포 산출물에 실주문 구현이 없다 | ✅ 충족 | `tests/e2e/test_phase0_completion.py::test_broker_registry_rejects_any_non_paper_broker_name`, `::test_no_real_broker_order_submission_exists_in_source_tree` (소스 트리 전체 정적 스캔) |

## 조건 2 해소 기록 (2026-07-18)

사용자가 조회 전용 키를 macOS Keychain(service `skhy-research`)에 주입하고,
실제 `adapters/providers/krx/`, `kis/`, `toss/` 어댑터로 read-only capability
probe를 실행해 세 공급자 응답을 확인했다. G-02는 `CONFIRMED`로 기록됐다.

재현·재검증 경로:

1. 조회 전용 키를 Keychain에 등록한다: `uv run keyring set skhy-research <NAME>`
   (`KRX_API_KEY`, `KIS_APP_KEY`, `KIS_APP_SECRET`, `TOSS_CLIENT_ID`,
   `TOSS_CLIENT_SECRET`). `.env`는 필요 없다 — 백엔드로 Keychain을 쓴다.
2. smoke를 Keychain 백엔드로 실행한다:
   `SKHY_SECRET_BACKEND=keychain uv run pytest -m smoke tests/e2e/test_provider_smoke.py -v`
   (기본 백엔드는 `env`이므로 이 변수를 지정하지 않으면 키가 Keychain에 있어도 skip된다.)
3. capability 증거를 재검증한다:
   `shasum -a 256 docs/decisions/gates/evidence/G-02-capability-probe.json`
   → `docs/decisions/gates/G-02.md`의 `원본 증거 checksum`과 일치해야 한다.

## 재현 명령

```bash
uv run ruff check src tests
uv run pyright
uv run pytest            # smoke 제외 전체 (실제 키 불필요)
SKHY_SECRET_BACKEND=keychain uv run pytest -m smoke   # 조건 2 전용 (Keychain 키 필요)
```
