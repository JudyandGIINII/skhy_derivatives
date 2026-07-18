# Phase 1 완료조건 점검 (`implementation_plan.md` 5.3)

| # | 완료조건 | 상태 | 근거 |
| --- | --- | --- | --- |
| 1 | 신뢰 가능한 H1 데이터가 최소 120 KRX 거래일이며 60/30/30 시간순 분할과 이후 walk-forward 결과가 재현된다 | ⚠️ 메커니즘만 충족 (실데이터 보류) | 파이프라인은 `tests/integration/test_krx_backfill_pipeline.py`(120거래일 합성 데이터)와 `tests/unit/test_time_splits.py`(60/30/30, walk-forward)로 실증. **실데이터 채움은 G-04/G-06 해소와 실제 KRX 키가 필요** — 두 게이트 모두 `docs/decisions/gates/`에서 `UNKNOWN` |
| 2 | 15:10 시점 신호에 사후 공개 AUM/NAV가 포함되지 않았다는 lineage 감사가 통과한다 | ✅ 충족 | `tests/e2e/test_h1_lookahead_lineage_audit.py`: 정상 케이스(전일 공개 NAV)는 raw→normalized→signal lineage로 역추적되고, 위반 케이스(당일 장후 확정 NAV)는 `LookaheadViolationError`로 신호·lineage 자체가 생성되지 않음을 실증 |
| 3 | 기본 비용과 각 비용 2배 결과, 집중도, 신뢰구간, 반증 지표를 포함한 리포트로 H1을 PASS/HOLD/REJECT로 판정한다 | ✅ 메커니즘 충족 | `experiments/statistics.py`(기대값·PF·MDD·집중도·bootstrap CI·permutation), `engine/cost_model.py`(`CostBreakdown.stressed(2x)`), `experiments/promotion.py`(PASS/HOLD/REJECT). `tests/e2e/test_h1_pipeline_end_to_end.py`가 feature→strategy→체결→통계→판정 전체 배선을 실증(합성 데이터로 PASS 판정까지 도달) |
| 4 | G-03 미확정이면 완전모델을 가장하지 않고 축소모델 버전과 품질 경고를 명시한다 | ✅ 충족 | `features/h1_close_pressure/close_pressure.py`: `observable_flow_adjustment`가 결측이면 `model_version="reduced"`로 낮추고 결측 상품을 `missing_flow_fund_ids`에 남김(`tests/unit/test_h1_close_pressure.py`). G-03은 여전히 `UNKNOWN` |

## 조건 1이 보류인 이유와 해소 경로

Phase 0와 동일한 근본 제약: 이 세션은 실제 KRX Open API 키가 없다. 백필
파이프라인(`application/krx_backfill.py`, `application/parquet_snapshot.py`,
`application/trading_day_coverage.py`)과 시간순 분할(`experiments/splits.py`)은
모두 구현·테스트됐고 120거래일 분량의 합성 데이터로 정합성을 실증했지만,
PRD가 요구하는 "신뢰 가능한" 데이터는 실제 KRX 종가·상품 기준정보여야 한다.

해소 경로:

1. G-06(데이터 이용조건) → G-02(KIS/Toss capability) → G-04(레버리지 상품
   universe) 순서로 해소한다 (`implementation_plan.md` 7.2).
2. 실제 `adapters/providers/krx/` 어댑터를 `ports/historical_data.py`,
   `ports/reference_data.py` 계약에 맞춰 구현한다(현재 `fixture_*` 구현이
   참고 형태).
3. `application/krx_backfill.backfill_daily_bars()`로 실제 120거래일 이상을
   백필하고 `trading_day_coverage.verify_trading_day_coverage()`로 커버리지를
   확인한다.
4. `experiments/splits.chronological_split()`로 실제 데이터를 60/30/30
   분할하고, H1 전략을 학습·검증까지만 튜닝한 뒤
   `experiments/test_set_seal.SplitContaminationGuard`로 test 구간을 봉인한다.

## 재현 명령

```bash
uv run ruff check src tests
uv run pyright
uv run pytest                                    # smoke 제외 전체 (실제 키 불필요)
uv run pytest tests/e2e/test_h1_lookahead_lineage_audit.py -v
uv run pytest tests/e2e/test_h1_pipeline_end_to_end.py -v
uv run pytest tests/integration/test_krx_backfill_pipeline.py -v
```
