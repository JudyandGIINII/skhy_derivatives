# Phase 1 완료조건 점검 (`implementation_plan.md` 5.3)

| # | 완료조건 | 상태 | 근거 |
| --- | --- | --- | --- |
| 1 | 신뢰 가능한 H1 데이터가 최소 120 KRX 거래일이며 60/30/30 시간순 분할과 이후 walk-forward 결과가 재현된다 | ⚠️ 메커니즘만 충족 (실데이터 보류) | 파이프라인은 `tests/integration/test_krx_backfill_pipeline.py`(120거래일 합성 데이터)와 `tests/unit/test_time_splits.py`(60/30/30, walk-forward)로 실증. **실데이터 채움에는 G-04/G-06의 유효한 PostgreSQL 결정과 실제 KRX 키가 필요** — 사람용 문서에서 G-06은 `CONFIRMED`지만 G-04는 `IN_REVIEW`이므로 현재도 차단 |
| 2 | 15:10 시점 신호에 사후 공개 AUM/NAV가 포함되지 않았다는 lineage 감사가 통과한다 | ✅ 충족 | `tests/e2e/test_h1_lookahead_lineage_audit.py`: 정상 케이스(전일 공개 NAV)는 raw→normalized→signal lineage로 역추적되고, 위반 케이스(당일 장후 확정 NAV)는 `LookaheadViolationError`로 신호·lineage 자체가 생성되지 않음을 실증 |
| 3 | 기본 비용과 각 비용 2배 결과, 집중도, 신뢰구간, 반증 지표를 포함한 리포트로 H1을 PASS/HOLD/REJECT로 판정한다 | ✅ 메커니즘 충족 | `experiments/statistics.py`(기대값·PF·MDD·집중도·bootstrap CI·permutation), `engine/cost_model.py`(`CostBreakdown.stressed(2x)`), `experiments/promotion.py`(PASS/HOLD/REJECT). `tests/e2e/test_h1_pipeline_end_to_end.py`가 feature→strategy→체결→통계→판정 전체 배선을 실증(합성 데이터로 PASS 판정까지 도달) |
| 4 | G-03 미확정이면 완전모델을 가장하지 않고 축소모델 버전과 품질 경고를 명시한다 | ✅ 충족 | `features/h1_close_pressure/close_pressure.py`: `observable_flow_adjustment`가 결측이면 `model_version="reduced"`로 낮추고 결측 상품을 `missing_flow_fund_ids`에 남김(`tests/unit/test_h1_close_pressure.py`). G-03은 여전히 `UNKNOWN` |

## 조건 1이 보류인 이유와 해소 경로

백필 파이프라인(`application/krx_backfill.py`, `application/parquet_snapshot.py`,
`application/trading_day_coverage.py`)과 시간순 분할(`experiments/splits.py`)은
모두 구현·테스트됐고 120거래일 분량의 합성 데이터로 정합성을 실증했다. 하지만
G-04의 PCF·공개시각·복제방식 증거가 아직 미완성이며 실제 120거래일 백필도 실행하지
않았으므로, PRD가 요구하는 "신뢰 가능한" 실데이터 완료조건은 보류 상태다.

해소 경로:

1. G-04의 남은 PCF·공개시각·복제방식 증거를 확보해 정식 검토를 완료한다.
2. 검토 완료된 G-04/G-06 `GateDecision`을 PostgreSQL에 저장하고 아래 로더로
   runtime `GateRegistry`를 구성한다.
3. `application.krx_backfill.backfill_daily_bars()`로 실제 120거래일 이상을
   백필하고 `trading_day_coverage.verify_trading_day_coverage()`로 커버리지를
   확인한다.
4. `experiments/splits.chronological_split()`로 실제 데이터를 60/30/30
   분할하고, H1 전략을 학습·검증까지만 튜닝한 뒤
   `experiments/test_set_seal.SplitContaminationGuard`로 test 구간을 봉인한다.

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
잘못되면 시작 단계에서 거부한다. 현재 G-04는 `IN_REVIEW`이므로 이를 임의로
`CONFIRMED` seed해서는 안 되며, G-04가 정식 확정되고 두 결정 행이 DB에 저장되기
전까지 실제 백필 차단을 유지한다.

## 재현 명령

```bash
uv run ruff check src tests
uv run pyright
uv run pytest                                    # smoke 제외 전체 (실제 키 불필요)
uv run pytest tests/e2e/test_h1_lookahead_lineage_audit.py -v
uv run pytest tests/e2e/test_h1_pipeline_end_to_end.py -v
uv run pytest tests/integration/test_gate_decision_store.py -v
uv run pytest tests/integration/test_krx_backfill_pipeline.py -v
```
