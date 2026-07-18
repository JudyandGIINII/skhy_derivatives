# Phase 1 완료조건 점검 (`implementation_plan.md` 5.3)

| # | 완료조건 | 상태 | 근거 |
| --- | --- | --- | --- |
| 1 | 신뢰 가능한 H1 데이터가 최소 120 KRX 거래일이며 60/30/30 시간순 분할과 이후 walk-forward 결과가 재현된다 | ✅ daily-proxy 연구 완료 | 2026-07-18 dev DB의 실제 KRX `000660` 120거래일(2026-01-20~07-16)과 ETP NAV/IV·상장좌수 315 snapshot으로 `skhy-research backtest`를 2회 실행. 두 번 모두 data hash `3f8edad2…e946`, result hash `ab71cd76…2e88` 일치 |
| 2 | 15:10 시점 신호에 사후 공개 AUM/NAV가 포함되지 않았다는 lineage 감사가 통과한다 | ✅ 충족 | `tests/e2e/test_h1_lookahead_lineage_audit.py`: 정상 케이스(전일 공개 NAV)는 raw→normalized→signal lineage로 역추적되고, 위반 케이스(당일 장후 확정 NAV)는 `LookaheadViolationError`로 신호·lineage 자체가 생성되지 않음을 실증 |
| 3 | 기본 비용과 각 비용 2배 결과, 집중도, 신뢰구간, 반증 지표를 포함한 리포트로 H1을 PASS/HOLD/REJECT로 판정한다 | ✅ daily-proxy 실데이터 충족 | `application/h1_daily_proxy_walk_forward.py`가 feature→event engine 왕복체결→기본/2배 비용→기대값·PF·MDD·집중도·bootstrap CI·permutation→promotion을 연결. 실제 32거래 결과는 daily-proxy 승격 비대상 정책에 따라 `HOLD`이며 원 H1 성과와 합치지 않음 |
| 4 | G-03 미확정이면 완전모델을 가장하지 않고 축소모델 버전과 품질 경고를 명시한다 | ✅ 충족 | `features/h1_close_pressure/close_pressure.py`: `observable_flow_adjustment`가 결측이면 `model_version="reduced"`로 낮추고 결측 상품을 `missing_flow_fund_ids`에 남김(`tests/unit/test_h1_close_pressure.py`). G-03은 여전히 `UNKNOWN` |

## 실데이터 daily-proxy 실행 결과

- 기초자산: 실제 KRX `000660` Bar 120거래일, 2026-01-20~2026-07-16.
- ETP: ETF/ETN endpoint를 거래일·endpoint별 한 번씩 read-only 호출해 raw 240건과
  `KrxEtpDailySnapshot` 315건을 append-only 저장했다. 9개 상품의 최초 관측일은
  2026-05-27이며, 상장 전 날짜에는 snapshot을 소급 생성하지 않았다.
- 고정 연구 파라미터: `kappa=0.10`, neutral band `0.001`, seed `7`. kappa는 test 결과를
  보고 조정하지 않았으며 결과에는 `explicit-fixed-research-parameter`로 기록된다.
- 60/30/30: train 2026-01-20~04-17, validation 04-20~06-04, test 06-05~07-16.
- anchored walk-forward: fold 1은 위 train→validation 구간(3거래), fold 2는
  2026-01-20~06-04 확장 train→06-05~07-16 test(29거래)다.
- 체결은 실제 KRX daily Bar의 시가·종가를 연구용 quote event로 재생한 paper 체결이다.
  15:10 실시간 호가나 실행 가능성 증거로 승격하지 않는다.
- 집계 base: 32거래, PnL -950,340.9162원, expectancy -29,698.1536원,
  PF 0.53337, MDD 1,147,494.6579원(초기자본 대비 11.47495%), expectancy 95% bootstrap
  CI [-72,603.8359, 10,811.0574], permutation p=0.911.
- 집계 2배 비용: PnL -1,113,681.8323원, expectancy -34,802.5573원,
  PF 0.47807, MDD 1,290,989.3159원(12.90989%).
- 판정: `h1_krx_daily_proxy_reduced_v1`은 `promotion_eligible=False`, resolution
  `daily-proxy`, scope `h1-daily-proxy-research-only`이므로 성과와 무관하게 `HOLD`다.
  원래 15:10 H1(`h1-original`)의 PASS/REJECT 근거로 병합하지 않는다.

## 런타임 gate 결정 로드

`docs/decisions/gates/*.md`는 검토자에게 근거·맥락·남은 gap을 설명하는 사람용
기록이다. 애플리케이션은 Markdown을 파싱하지 않으며, PostgreSQL `gate_decision`
append-only journal의 gate별 최신 행을 기계용 진실의 출처로 사용한다.

```python
from skhy_research.adapters.persistence.gate_decision_store import PostgresGateDecisionStore
from skhy_research.application.gate_registry_loader import load_gate_registry

store = PostgresGateDecisionStore(engine)
store.save_decision(reviewed_gate_decision)  # 별도 승인·seed 절차에서 실행
gate_registry = load_gate_registry(store)

result = backfill_daily_bars(
    ...,
    gate_registry=gate_registry,
    gate_as_of_utc=as_of_utc,
)
```

로더는 `GateRegistry.record_decision()`을 그대로 통과하므로 `CONFIRMED` 행에 URL,
SHA-256 checksum, 결론, 담당 provider, 확인시각, 유효기간이 없거나 시간 범위가
잘못되면 시작 단계에서 거부한다. 사람용 G-04는 축소 범위에서 `CONFIRMED`지만 문서가
DB journal을 대신하지 않는다. 2026-07-18 실행에서는 검토된 G-04/G-06 결정 행이 dev DB에
저장되어 실제 백필 gate를 통과했으며, 결정이 없거나 만료된 다른 환경에서는 계속 차단한다.

## 재현 명령

```bash
uv run ruff check src tests
uv run pyright
uv run pytest                                    # smoke 제외 전체 (실제 키 불필요)
uv run pytest tests/e2e/test_h1_lookahead_lineage_audit.py -v
uv run pytest tests/e2e/test_h1_pipeline_end_to_end.py -v
uv run pytest tests/integration/test_gate_decision_store.py -v
uv run pytest tests/integration/test_krx_backfill_pipeline.py -v

SKHY_SECRET_BACKEND=keychain \
SKHY_DATABASE_URL=postgresql+psycopg://skhy:skhy_local_dev@localhost:5432/skhy_research \
uv run skhy-research backfill-etp --trading-days 120 --pace-seconds 0.25

SKHY_SECRET_BACKEND=keychain \
SKHY_DATABASE_URL=postgresql+psycopg://skhy:skhy_local_dev@localhost:5432/skhy_research \
uv run skhy-research backtest --seed 7 --trading-days 120 --kappa 0.10 \
  --neutral-band 0.001 --bootstrap-resamples 1000 --permutations 1000 --json
```
