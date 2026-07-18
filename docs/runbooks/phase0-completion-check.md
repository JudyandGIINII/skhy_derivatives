# Phase 0 완료조건 점검 (`implementation_plan.md` 5.2)

`implementation_plan.md`가 정의한 Phase 0 완료조건 4가지와 현재 상태.

| # | 완료조건 | 상태 | 근거 |
| --- | --- | --- | --- |
| 1 | fixture 기반 공급자 계약·정규화·캘린더·재시작 테스트가 통과한다 | ✅ 충족 | `tests/contract/test_fixture_provider_contract.py`, `tests/unit/test_normalizer.py`, `tests/unit/test_calendar_resolver.py`, `tests/integration/test_raw_recorder.py`(재시작 catch-up) |
| 2 | 사용자가 조회 전용 키를 주입한 환경에서 KRX/KIS/Toss capability smoke가 통과하거나, 실패 capability가 명시적으로 비지원·차단 상태로 기록된다 | ⏸️ 보류 (실제 키 필요) | `tests/e2e/test_provider_smoke.py`에 스캐폴딩만 존재. 실제 KRX/KIS/Toss 어댑터(SDK 연동)는 Phase 1에서 G-02 해소 후 구현 예정. 이 세션에는 실제 API 키가 없어 검증 불가 — **알려진 한계**로 남긴다. |
| 3 | 원시 레코드 하나에서 source, 수신시각, checksum, 이용조건, 정규화 레코드까지 추적된다 | ✅ 충족 | `tests/e2e/test_phase0_completion.py::test_raw_record_is_traceable_to_normalized_record` (raw_recorder → normalizer → lineage_edge 실제 PostgreSQL round trip) |
| 4 | broker registry와 배포 산출물에 실주문 구현이 없다 | ✅ 충족 | `tests/e2e/test_phase0_completion.py::test_broker_registry_rejects_any_non_paper_broker_name`, `::test_no_real_broker_order_submission_exists_in_source_tree` (소스 트리 전체 정적 스캔) |

## 조건 2가 보류인 이유와 해소 경로

이 작업 세션은 사용자의 실제 KRX/KIS/Toss 조회 전용 API 키에 접근할 수 없다.
`ports/{market_data,reference_data,historical_data}.py` 포트 계약과
`adapters/providers/fixture_*` (P0-07)로 계약 형태는 실증했지만, 실제 KRX
Open API·KIS Open API·토스증권 Open API를 호출하는 어댑터 구현체는 아직
없다.

해소 경로:

1. 사용자가 `.env`에 조회 전용 키를 주입한다 (`.env.example` 참고).
2. Phase 1에서 실제 `adapters/providers/krx/`, `kis/`, `toss/` 어댑터를
   `ports/*.py` 계약에 맞춰 구현한다(현재 `fixture_*` 구현이 그 참고 형태다).
3. `application.capability_probe.run_capability_probe()`로 실제 capability를
   조회해 `docs/decisions/gates/G-02.md`를 갱신한다.
4. `SKHY_ENV=smoke uv run pytest -m smoke`로 `tests/e2e/test_provider_smoke.py`를
   실행해 조회 전용 호출이 성공하는지 확인한다.

## 재현 명령

```bash
uv run ruff check src tests
uv run pyright
uv run pytest            # smoke 제외 전체 (실제 키 불필요)
uv run pytest -m smoke   # 조건 2 전용 (실제 키 필요, 현재는 skip 또는 fail)
```
