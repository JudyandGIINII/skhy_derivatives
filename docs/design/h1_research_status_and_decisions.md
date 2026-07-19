# H1 연구 현황·의사결정 통합 문서

> 상태: `LIVING` — 2026-07-19 기준 H1(종가 리밸런싱·구조적 수급) 대안 탐색 종합<br>
> 용도: 페이퍼 전용 연구. 실주문 제출·투자권유·수익보장 아님<br>
> 상위 요구사항: [`prd.md`](../../prd.md) 7.1, 9.1, 10.2~10.6, 14.3<br>
> 이 문서는 여러 설계·게이트 문서를 잇는 인덱스이며, 각 항목의 단일 진실은 `prd.md`다.

## 0. TL;DR — 한 문단

원 15:10 H1 완전모델은 종가 예상체결·프로그램 순불균형이 무료 데이터에 없어 **G-03 BLOCKED**다. 이를 우회하려는 모든 종목별 수급 신호(프로그램·투자자·공매도)는 **KRX Open API에 없고 웹 수동 CSV 전용**임이 실키로 확인됐다. 레버리지 상품 기반 구조적 모델은 국내 상품 실이력이 **~35거래일**뿐이라 PRD 120일 미충족으로 **HOLD**다. 결과적으로 현재 **자동 실행으로 PASS를 낼 수 있는 H1 경로는 없으며**, 남은 길은 (1) 라이브 수집으로 종목별 프로그램·콜구간 데이터를 앞으로 축적, (2) 수동 CSV로 종목별 수급을 채워 "약한" 사전반증, (3) 데이터 벽을 우회하는 종가 오버슈팅 **되돌림(mean-reversion)** 백테스트다.

## 1. 데이터 가용성 지도 (이 탐색의 핵심 발견)

| 신호/데이터 | KRX Open API(자동, 실키) | KRX 웹 수동 CSV | KIS 라이브 | 비고 |
| --- | --- | --- | --- | --- |
| 일별 OHLCV·거래대금 | ✅ `stk_bydd_trd` | ✅ | ✅ | 확보됨(485거래일, 2024-07-17~2026-07-16) |
| KOSPI 지수 | ✅ | ✅ | ✅ | 확보 |
| KRX 반도체 등 시리즈 지수 | ⚠️ 현재 키 **권한 없음** | ✅ | — | 통제변수 결측 처리 |
| 시장전체 투자자별 flow | ✅ | ✅ | — | 시장 aggregate만 |
| **종목별 투자자별 순매수** | ❌ **미제공** | ✅ [12002]류 | ✅(실시간) | 000660 2년 CSV 사용자 보유(`data_2121`) |
| **종목별 프로그램매매** | ❌ **미제공**(시장전체 [12012]만) | ❓ 종목별 과거 이력 없음 | ✅ `H0STPGM0`(실시간) | 종목별 과거는 라이브로만 축적 |
| **종목별 공매도([MDCSTAT300])** | ❌ **미제공** | ✅ | ✅ | 잔고 T+2·거래량 t-1 |
| 종가 단일가(경매) 예상체결 순불균형 | ❌ | ❌ | ⚠️ 15:20~15:30 실시간(추정만) | G-03 BLOCKED 근거 |
| ETF/ETN NAV·iNAV·상장좌수 | ✅(daily-proxy가 사용) | ✅ | — | 레버리지 상품은 2026-05-27~ 상장 |

**결론**: KRX 무료 자동 API는 사실상 **가격·지수·시장 aggregate**만이다. H1이 필요로 하는 **종목별 수급은 전부 수동 CSV 또는 라이브 수집**을 요구한다.

## 2. 구현된 H1 모델 변형 현황

| 모델 버전 | 상태 | 핵심 제약 | 코드 |
| --- | --- | --- | --- |
| `h1_krx_daily_proxy_reduced_v1` | 구현·검증 | 승격 불가 proxy. 32거래 기대값 −29,698원, PF 0.53, 2배비용 음수 | 기존 |
| `h1_original_1510_full_v1` | **G-03 BLOCKED** | 종가 예상체결 순불균형 무료 부재. 파이프라인만 구현, 검증 HOLD | `application/h1_original_validation.py` |
| `h1_kis_estimated_auction_flow_v1` | **REJECTED_STRUCTURAL** | 15:10 antc 전제·crossed-book 산식 붕괴(3모델 3라운드 검증) | 설계 폐기 |
| `h1_kis_continuous_ofi_program_proxy_v1` | 구현, 실데이터 **HOLD** | 라이브 수집 전. OFI+KRX 프로그램, cheap-reject 하네스 | `features/h1_close_pressure/continuous_ofi.py`, `application/h1_continuous_ofi_proxy.py` |
| `h1_kis_close_call_indicative_v1` | 설계·PRD개정 승인 | 15:25/15:28 시간선. 라이브 콜구간 데이터 축적 필요 | 수집기가 15:20~15:30 indicative 영구보존 |
| `weak_daily_v1` (사전반증) | 구현, 실데이터 **HOLD** | 종목별 프로그램 CSV 필요. FALSIFY 신뢰 금지(false-negative) | `application/h1_prefalsification_study.py` |
| G9 특이 투자자-flow (사전반증) | 구현, 실데이터 **HOLD** | 종목별 투자자·공매도 CSV 필요 | `prefalsification/general_flow_study.py`, `features/g9_idiosyncratic_flow.py` |
| S8 구조적 레버리지 앙상블 | 골격, **HOLD** | 레버리지 상품 실이력 ~35일 < 120일 | `features/s8_structural_leverage.py` |

## 3. 알고리즘 인벤토리 (대안 탐색)

두 CLI(Codex·agy)의 독립 브레인스토밍을 병합. `(promise × 데이터 가용성 × PRD 준수)` 관점.

### 🅐 메커니즘-충실 트랙 (H1 이론 그대로, 데이터 기근)
- 리밸런싱 델타: `Σ βᵢ(βᵢ−1)·NAVᵢ,ₜ₋₁·Sharesᵢ,ₜ₋₁·R_underlying,ₜ / ADV`
- 설정·환매 flow: `Σ βᵢ·NAVᵢ,ₜ₋₁·ΔSharesᵢ` + NAV프리미엄·추적오차·capacity → **S8 앙상블**
- 제약: 레버리지 상품 ~35거래일 → 정식 backtest 불가(HOLD). 라이브 수집·시간 축적으로만 해소.

### 🅑 데이터-풍부 트랙 (장기 이력, 메커니즘 약함)
- G9 000660 특이 투자자-flow(시장·삼성·반도체 통제 후 잔차 순매수)
- 공매도 스퀴즈(잔고 T+2·거래량 t-1), 회원사 바스켓 집중도, 종가경매 참여도(주로 결과)
- 제약: 종목별 데이터가 수동 CSV 전용 → 즉시 실행 불가. 통과해도 **H1 증명 아님**(약한 청신호), 실패해도 리밸런싱 기각 아님.

### 🅓 검증 설계 (신호 아님, 필수 안전장치)
- D3 음성대조(상품 없던 시기·유사 반도체주·가짜 상장일), D1 matched event study, D2 상장 전후 DiD.

## 4. 개인투자자 현실 우위 (합법 범위)

공개규칙 기반 기계적 flow만 대상(비공개주문·내부정보·시세조종·타인주문 선행매매 배제, PRD 3.2). 상세: [`h1_flow_anticipation_retail_edge.md`](h1_flow_anticipation_retail_edge.md).

- **함정(정직한 열위)**: 순진한 "선행 탑승"은 HFT가 14:50 이전 선점 → 개인은 기관의 **exit liquidity(털리는 쪽)**. 마찰비용 ~0.28%가 원시 기대(+0.20%)를 상쇄 → 순손실.
- **개인의 진짜 우위(반직관)**: ① 강제체결 의무 없음을 역이용한 **선택적 유동성 공급**, ② **종가 오버슈팅의 익일 시가 되돌림(mean-reversion fade)** — 지연경쟁 회피, 비용 후 유리, 그리고 **데이터 벽 우회**(daily-proxy NAV/상장좌수 + KIS 30일 분봉으로 백테스트 가능).

## 5. 미결정·다음 단계

| 경로 | 데이터 실재성 | 즉시 실행 | 성격 |
| --- | --- | --- | --- |
| 라이브 수집 착수(수집기·스케줄러 대기) | 앞으로 축적 | 다음 거래일~ | 종목별 프로그램·콜구간의 유일한 정공 |
| G9 실행용 수동 CSV 5종 확보 | 사용자 다운로드 | 확보 즉시 | 약한 사전반증(FALSIFY 신뢰 금지) |
| **mean-reversion fade 백테스트** | 대부분 보유 | 즉시 가능 | 데이터 벽 우회, 개인 우위 실재 가능 |
| S8 구조적 정식 검증 | ~85거래일 더 | 4~5개월 후 | H1 가장 충실 |

## 6. 문서·게이트 인덱스

- 설계: [`h1_estimated_flow_model.md`](h1_estimated_flow_model.md)(Round3 봉인·REJECTED_STRUCTURAL 기록), [`prd_9_1_amendment_proposal.md`](prd_9_1_amendment_proposal.md)(APPROVED), [`h1_historical_backtest_feasibility.md`](h1_historical_backtest_feasibility.md), [`h1_prefalsification_methodology_notes.md`](h1_prefalsification_methodology_notes.md), [`h1_flow_anticipation_retail_edge.md`](h1_flow_anticipation_retail_edge.md)
- 게이트: [`G-03`](../decisions/gates/G-03.md)(BLOCK), [`G-03 investigation`](../decisions/gates/evidence/G-03-investigation.md)
- 운영: [`h1_collection_runbook.md`](../runbooks/h1_collection_runbook.md)
- 안전 인프라: FR-14 리스크 엔진(`src/skhy_research/risk/`), 비용 completeness fail-closed(`engine/cost_model.py`)
