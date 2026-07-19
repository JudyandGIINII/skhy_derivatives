# H1 추정 flow 모델 상세 설계 v1 — Round 3 최종 봉인본

> 상태: `ROUND 3 SEALED` — 공유 raw 수집기 fixture 구현 완료, live capture·OFI feature·회귀는 미완료<br>
> 용도: 페이퍼 전용 연구. 실주문 제출·투자권유·수익보장 아님<br>
> 기준일: 2026-07-19<br>
> 상위 요구사항: [`prd.md`](../../prd.md) 7.1, 9.1, 10.2~10.6, 14.3<br>
> 관련 gate: [`G-03`](../decisions/gates/G-03.md),
> [`G-03 investigation`](../decisions/gates/evidence/G-03-investigation.md)

## 1. 최종 결론과 병행 플랜

Round 1의 핵심 전제였던 **15:10 종가 단일가 예상체결 정보는 사용할 수 없다.**
KRX 종가 단일가 주문접수와 종가 예상체결가 산출은 15:20부터 시작하는 반면,
PRD 9.1의 신규 주문의도 마감은 15:19:30이다. 따라서 현재 규칙에서

```text
종가경매 indicative 가용구간 ∩ 의사결정 허용구간 = ∅
```

이다. 저장소의 G-03 조사도 `H0STASP0` 예상체결 수신 구간을 15:20~15:30으로
기록한다. 15:10의 `antc_cnpr`·`antc_vol`을 종가경매 값으로 볼 근거는 없으며,
공란·시가 단일가·VI 잔존값을 종가 정보로 재해석하지 않는다.

또한 Round 1의 `B_10`·`S_10` 산식은 `p_e`를 기준으로 양쪽 호가가 동시에 체결
가능해야 한다. 15:10 연속장의 정상적인 uncrossed book에서는 `M_10=0`으로 퇴화한다.
이는 낮은 신뢰도가 아니라 **산식의 시장상태 불일치**다.

따라서 다음을 확정한다.

1. `h1_kis_estimated_auction_flow_v1`은 구현 전 `REJECTED_STRUCTURAL`로 종료한다.
   depth-only 모델로 이름만 바꾸거나 0으로 대체하지 않는다.
2. 현 PRD를 지키는 주 경로는 별도 버전
   **`h1_kis_continuous_ofi_program_proxy_v1`**이다. 15:00~15:10 KRX 연속장 OFI와
   KRX 프로그램매매를 쓰는 저비용 반증용 연구 모델이며 기대 수준은 낮게 둔다.
3. 정공법인 **`h1_kis_close_call_indicative_v1`**은 15:20 이후 종가경매
   indicative 시계열을 쓰되, PRD 9.1의 snapshot·주문의도 마감 개정에 대한
   사용자 승인이 선행되어야 한다. 이 문서는 의존성만 기록하며 `prd.md`를 수정하지 않는다.
4. 사용자가 선택한 **병행 플랜**에 따라 14:59:50~15:30:10의 연속장·종가경매 raw를
   같은 read-only 수집기로 보존한다. 연속장 proxy 연구를 값싸게 기각하는 동안에도
   15:20~15:30의 `ANTC_CNPR`·`ANTC_VOL` 시계열은 첫날부터 별도 1급 레코드로 쌓는다.
   수집은 PRD 개정이 아니며, 종가경매 값을 현 15:10 신호에 쓰는 것도 아니다.

## 2. Round 1~2 비평 판정

| ID | 판정 | 근거와 Round 2 조치 |
| --- | --- | --- |
| 치명-A | **수용·반증 확정** | 15:20 시작 종가경매 정보와 15:19:30 의사결정 마감은 겹치지 않는다. 15:10 `antc_*` 전제를 폐기한다. 추가 20일 probe로 되살릴 쟁점이 아니다. |
| 치명-B | **수용·반증 확정** | 연속장 uncrossed book에서 Round 1의 `M_10`은 구조적으로 0이다. `B_10/S_10`, `V_e/M_10`, `VISIBLE_MATCH_ZERO` 기반 산식을 전부 삭제한다. |
| 중대-C | **수용** | 주피드를 KRX `H0STPGM0`, 교차검증을 통합 `H0UNPGM0`으로 고친다. `H0NXPGM0`은 모델 입력이 아니다. 세 venue 값을 합산하지 않는다. G-03 조사 문서 정정은 이번 파일 범위 밖이므로 11절에 부채로 등록한다. |
| 중대-D | **수용** | `V_e/M_10` 곱셈·clip을 폐기한다. 연속장 OFI, depth imbalance, microprice gap, KRX 프로그램 flow, 부호충돌을 개별 feature로 회귀에 넣는다. 향후 경매 모델도 `I_10`, `V_e`, coverage, `p_e-microprice`, 부호충돌을 각각 투입하며 곱하지 않는다. |
| 중대-E | **수용** | 성과 probe보다 먼저 입력 5종의 원천·공표시각·인과가용성을 판정한다. 당일 설정·환매의 15:10 이전 원천이 없으면 그 항을 `0`으로 넣지 않고 명시적으로 제외한 별도 버전을 쓴다. |
| 보통-F | **수용** | 0.60 cap과 HIGH/MEDIUM/LOW 상수를 삭제한다. 데이터 적격성은 hard filter, 확률 신뢰도는 학습 외 calibration 진단으로만 다룬다. 등급 숫자가 아니라 선택 절차를 사전등록한다. |
| 보통-G | **수용** | target을 사전 정의한 실행 가능 진입기준가 대비 종가수익으로 통일한다. Huber elastic-net의 loss와 탐색범위를 명문화하고 `sign × auction_notional` 합성 target을 삭제한다. |
| 경미-H | **수용** | probe의 수치 판정기준을 사전등록한다. A1은 규정 시간선과 1~2일 원시 캡처만으로 선판정하며, 이미 구조적 반증으로 닫는다. |

반박으로 유지하는 항목은 없다. C에서 G-03 파일을 즉시 고치지 않는 이유는 사실관계
반박이 아니라 해당 파일이 다른 에이전트의 병렬 작업 범위이기 때문이다.

### 2.1 Round 3 Fable 지적 판정

| ID | 판정 | 최종 봉인 |
| --- | --- | --- |
| 중대-1 | **수용** | `delta_multiplier`·`lambda`·`rho`는 train 내부 expanding fold로만 선택한다. v1 regime은 단일 regime으로 고정한다. validation은 후보 선택·threshold 조정 없이 순수 판정에만 쓴다. |
| 중대-2 | **수용** | `k=2.0`, 2천만원 고정 배정자본, 100만원 고정 목표명목, 2초 FOK 진입, 15:30:10 종가경매 outcome 마감을 봉인한다. theoretical-only도 동일 target·loss·fill·cost 계약을 쓴다. |
| 보통-3 | **수용** | OFI는 같은 날 10분 시간가중 평균 best depth로 나누고, depth imbalance도 시간가중 평균으로 바꾼다. `H0STCNT0` 진단과 2초 최대 event gap을 수집·probe 계약에 추가한다. |
| 보통-4 | **수용** | L1이 `kappa=0`을 만들면 `THEORY_TERM_ELIMINATED`를 남긴다. target 결측률·사유와 변동성 분포를 split별 필수 보고한다. |
| Q3 | **확정** | 당일 15:10 이전 무료 설정·환매 원천은 없음으로 봉인한다. 0 대체 없이 제외하고 T-1 상장좌수 변화 변형을 별도 버전으로 사전등록한다. |
| Q4 | **확정** | KRX program semantics는 단조성·항등식·EOD 3원 대사·session reset·통합 대사의 5단계가 모두 통과해야 봉인된다. |

## 3. 모델 신분과 승격 경계

| 모델 버전 | 상태 | 입력 해상도 | 허용 범위 |
| --- | --- | --- | --- |
| `h1_original_1510_full_v1` | `G-03 BLOCKED` | 원천 종가 불균형 포함 intraday | 원 15:10 H1. 현 무료 피드로 검증 불가 |
| `h1_kis_estimated_auction_flow_v1` | **`REJECTED_STRUCTURAL`** | 잘못 가정한 15:10 indicative | 구현·재사용 금지 |
| **`h1_kis_continuous_ofi_program_proxy_v1`** | **봉인된 주 경로** | 15:00~15:10 continuous L1/L10 + KRX program | 저비용 반증 연구. 원 H1 승격 근거가 아님 |
| `h1_kis_continuous_ofi_program_lagged_creation_proxy_v1` | `PREREGISTERED_VARIANT` | 위 입력 + T-1 상장좌수 변화 | base와 합치지 않고 별도 60/30/30 검증 |
| `h1_kis_close_call_indicative_v1` | `BLOCKED_BY_PRD_AMENDMENT` | 15:20 이후 call indicative 시계열 | PRD 9.1 개정 승인 후 별도 설계·검증 |
| `h1_krx_daily_proxy_reduced_v1` | 별도 기존 경로 | KRX 일별 | 이 문서의 결과와 합산 금지 |

주 경로가 `PASS`하더라도 의미는 `continuous-flow-proxy` 연구 scope의 통계·비용 gate를
통과했다는 것뿐이다. 원 15:10 H1, G-03 또는 종가경매 imbalance가 검증됐다는 뜻이
아니다. PRD 9.1이 예상체결 피드 부재 시 차단을 요구하므로, 주 경로는 Round 3에서
봉인한 대로 **read-only 수집·연구 백테스트만** 수행한다. 여기서 정의한 paper intent와
fill은 과거·수집 데이터의 모의 실행 계약이며 실제 주문 제출 권한이 아니다.

## 4. 두 경로의 인과 시간선

### 4.1 채택 경로: 연속장 OFI proxy

| 시각 (KST) | 처리 | 인과 규칙 |
| --- | --- | --- |
| 14:59:50 이전 | 연결·schema 확인 | feature 계산 전 연결 안정화 |
| **15:00:00~15:10:00** | `000660` KRX 호가·체결과 program 수집 | 주 feature는 `H0STASP0`, `H0STPGM0`; `H0STCNT0`, `H0UNPGM0`, `H0NXPGM0`은 진단 전용 |
| **15:10:00** | 고정 signal snapshot | `event_time <= 15:10:00`만 사용 |
| 15:10:00~15:19:30 | 품질판정·paper 실행 simulation | feature 재계산 금지, 실제 실행 가능 quote 기록 |
| **15:19:30** | 신규 주문의도 마감 | 이후 intent 차단 |
| 15:20:00~15:30:00 | 종가경매 indicative·outcome 수집 | `ANTC_CNPR`·`ANTC_VOL`을 별도 1급 raw로 영구 보존. 현 proxy feature 사용 금지 |
| 15:30:00~15:30:10 | 지연 packet·종가 outcome 마감 | raw는 보존하되 provider event time을 바꾸지 않음 |

이 경로는 종가경매 imbalance를 추정한다고 주장하지 않는다. 연속장 주문흐름이 종가까지
남는지 값싸게 반증하는 **continuous-flow proxy**다.

### 4.2 보류 경로: 실제 종가경매 indicative

`h1_kis_close_call_indicative_v1`은 15:20 이후 `p_e(t)`·`V_e(t)` 시계열을 원래
가용시각 그대로 사용한다. 이를 실행하려면 최소한 다음 PRD 변경안에 대한 사용자 승인이
필요하다.

- 신호 snapshot을 종가 단일가 시작 이후의 정확한 시각으로 이동
- 신규 주문의도 마감도 그 이후로 이동하되 KRX 주문접수 종료보다 앞서 고정
- 새 snapshot부터 intent까지의 지연·freshness·취소 규칙 정의
- 경매 참여 주문의 체결·시장충격·비용 모델을 연속장 진입과 분리

승인 전에는 시간을 임의로 정하지 않고, 15:20 이후 데이터를 15:10 feature로 소급하지
않는다. `prd.md` 9.1은 이 문서에서 변경하지 않는다.

## 5. 성과 probe 이전 입력원천 설계 gate

### 5.1 비평 대상 5종

| 입력군 | 무료 원천·필드 | 원천 시각·공표시각 | 15:10 인과가용성 | 최종 봉인 처리 |
| --- | --- | --- | --- | --- |
| 종가 예상체결가 `p_e` | KIS `H0STASP0 ANTC_CNPR`, REST `antc_cnpr` | 종가 단일가 15:20 이후 | **불가** | 주 경로에서 제거. 경매 경로에서만 사용 |
| 종가 예상체결수량 `V_e` | `H0STASP0 ANTC_VOL/ANTC_CNQN`, REST `antc_vol` | 종가 단일가 15:20 이후 | **불가** | 주 경로에서 제거. 필드 의미 확인 전 상호대체 금지 |
| 프로그램매매 | 주: KRX `H0STPGM0`; 진단: 통합 `H0UNPGM0` | provider event time, 장중 실시간 후보 | **조건부 가능** | 단위·누적/증분·reset·venue 의미 probe 통과 전 hard missing |
| 10단계 호가 depth | KRX `H0STASP0 BIDP/ASKP`, `*_RSQN` | provider 호가시각, 연속장 실시간 | **조건부 가능** | 15:00~15:10 OFI·depth feature. 10단계 crossed-match 계산 금지 |
| 당일 설정·환매 추정 | 15:10 이전 공식 무료 원천 **없음으로 봉인** | 사후/T+1 공표 | **불가** | 기본 버전에서 항 자체를 제외하고 `NET_CREATION_SOURCE_UNAVAILABLE` 기록 |

이 표의 `조건부 가능`은 API 이름만으로 확정하지 않는다. 계정의 prod read-only 구독권한,
원시 단위, event timestamp, 지연과 schema를 8절 기준으로 통과해야 한다.

### 5.2 H1 구조 입력

| 입력 | 허용 원천시점 | 처리 |
| --- | --- | --- |
| `prior_nav_i`, `prior_aum_i` | 전 거래일 장 종료까지 공개되고 15:10 전에 수집된 공식값 | 당일 장후 확정값 사용 금지 |
| `beta_i` | 15:10 전에 유효한 공식 상품조건 | 미확인 시 해당 상품 제외 |
| `replication_type_i` | 전일까지 공개된 상품 문서·검증된 reference | `UNKNOWN`이면 해당 상품 flow 제외, 임의 multiplier 금지 |
| `underlying_return_i(t)` | `000660`의 15:10 이하 가격 | 동일 event/as-of 규칙 적용 |
| `underlying_20d_adv_notional` | 15:10 전에 완결된 과거 20거래일 | 당일 거래대금 포함 금지 |

### 5.3 설정·환매 제외와 T-1 lagged creation 변형

기본 주 경로 `h1_kis_continuous_ofi_program_proxy_v1`은 인과적 원천이 없는
`net_creation_redemption`을 **0으로 입력하지 않고 feature schema에서 제외**한다.
`creation_term_status=EXCLUDED_UNAVAILABLE_SOURCE`를 모든 결과에 보존한다.

이 제외는 원 완전모델의 결측 해소가 아니다. 설정·환매가 중요한 날의 omitted-variable
bias가 예상되므로 기본 버전의 사전 기대를 낮추는 이유다.

사전등록 변형 `h1_kis_continuous_ofi_program_lagged_creation_proxy_v1`은 거래일 `t`의
15:10 전에 확정·수집된 `t-1`, `t-2` 공식 상장좌수와 `t-1` NAV만 사용한다.

```text
delta_listed_shares_lag1_i(t)
  = listed_shares_i(t-1) - listed_shares_i(t-2)

lagged_creation_notional_i(t)
  = delta_listed_shares_lag1_i(t) * nav_i(t-1)

x_lagged_creation(t)
  = sum_i replication_sign_i * lagged_creation_notional_i(t)
    / underlying_20d_adv_notional(t)
```

공표·수집시각 lineage가 15:10 이후면 `LAGGED_CREATION_NOT_CAUSAL`로 결측 처리한다.
상장좌수 변화는 실제 설정·환매 주문시각이나 당일 flow가 아니므로 base에 자동 추가하지
않고 별도 experiment ID·계수·60/30/30 결과를 갖는다. 유의한 증분가치가 없으면 변형만
`REJECT`하며 base 결과와 합산하지 않는다.

## 6. 연속장 feature와 추정식

### 6.1 Best-level OFI

15:00~15:10의 연속된 KRX best bid/ask event를 `n=1..N`이라 하자. `P^B`, `q^B`는
최우선 매수 가격·잔량, `P^A`, `q^A`는 최우선 매도 가격·잔량이다.

```text
e_n = 1[P^B_n >= P^B_(n-1)] * q^B_n
    - 1[P^B_n <= P^B_(n-1)] * q^B_(n-1)
    - 1[P^A_n <= P^A_(n-1)] * q^A_n
    + 1[P^A_n >= P^A_(n-1)] * q^A_(n-1)

OFI_10m = sum_(n=1..N) e_n
```

이는 Cont–Kukanov–Stoikov(2014)의 best-quote order flow imbalance 정의를 따른다.
그 논문은 짧은 구간의 가격변화와 OFI의 선형 관계 및 depth와 impact의 역관계를
보였지만, NYSE 표본의 결과가 KRX 종가수익 예측력을 보장하지는 않는다. 이 문서에서는
경제적 정당화가 아니라 값싼 반증 후보로만 사용한다.

### 6.2 개별 feature

모든 연속형 feature는 train 구간의 median/IQR로만 robust scaling한다. 0분산 feature는
삭제하고 그 사실을 schema에 남긴다.

```text
D_best(t) = (bid_qty_1(t) + ask_qty_1(t)) / 2

mean_best_depth_10m
  = [sum_n D_best(t_n) * (min(t_(n+1), 15:10) - t_n)] / 600 seconds

x_ofi = OFI_10m / mean_best_depth_10m

D_10(t)
  = [sum_l bid_qty_l(t) - sum_l ask_qty_l(t)]
    / [sum_l bid_qty_l(t) + sum_l ask_qty_l(t)], l=1..10

x_depth
  = [sum_n D_10(t_n) * (min(t_(n+1), 15:10) - t_n)] / 600 seconds

microprice  = (ask_1 * bid_qty_1 + bid_1 * ask_qty_1)
              / (bid_qty_1 + ask_qty_1)
midprice    = (bid_1 + ask_1) / 2
x_micro     = (microprice - midprice) / tick_size

x_program   = delta_15:00→15:10(KRX_program_net_buy_notional)
              / underlying_20d_adv_notional

x_conflict  = 1[sign(x_ofi) != sign(x_program)
                and x_ofi != 0 and x_program != 0]
```

`D_best`와 `D_10`은 각 호가 event부터 다음 event 직전까지 piecewise constant로 본다.
15:00:00을 덮는 마지막 사전 snapshot이 없거나 600초 전체를 덮지 못하면 계산하지 않는다.
따라서 `x_ofi`의 분모는 미래·train 통계가 아닌 **같은 날 15:00~15:10의 인과적 평균
best depth**다. `mean_best_depth_10m<=0`이면 `BEST_DEPTH_ZERO`로 차단한다.

`H0STPGM0`의 순매수대금이 cumulative이면 window 끝과 시작을 차분하고, incremental이면
event를 합산한다. semantics가 확정되기 전에는 어느 쪽도 가정하지 않는다.
`H0UNPGM0`은 통합값이라 `H0STPGM0`에 더하지 않으며 source-divergence 진단에만 쓴다.
`H0NXPGM0`도 주 feature가 아니다.

Round 1의 `I_10`, `V_e`, `coverage`, `p_e-microprice`, `price_book_sign_conflict`는
주 경로에 존재하지 않는다. 향후 PRD 개정 후 경매 모델을 설계할 경우에도 이들을
**각각 독립 feature**로 넣고 `V_e/M_10` 곱셈·clip은 되살리지 않는다.

### 6.3 PRD 압력식과 회귀

상품 `i`별 이론 exposure와 ADV 정규화 항은 다음과 같다.

```text
theoretical_delta_exposure_i(t)
  = beta_i * (beta_i - 1) * prior_nav_i * underlying_return_i(t)

z_i(t) = theoretical_delta_exposure_i(t) / underlying_20d_adv_notional(t)
```

설정·환매가 제외된 기본 버전의 회귀와 tradable pressure는 다음과 같다.

```text
y_t = alpha_train + sum_i kappa_i,regime * z_i(t)
      + theta_regime' * x_t + epsilon_t

observable_flow_adjustment_proxy(t)
  = underlying_20d_adv_notional(t) * theta_regime' * x_t

estimated_close_pressure_continuous_proxy(t)
  = sum_i kappa_i,regime * z_i(t) + theta_regime' * x_t
```

`alpha_train`은 target의 train 평균을 흡수하는 진단 intercept이며 신호·압력에는 넣지
않는다. `kappa`와 `theta` 및 모든 hyperparameter는 train 내부에서만 적합·선택한다.
L1 규제로 어떤 `kappa_i`가 정확히 0이면 결과에 `THEORY_TERM_ELIMINATED:<fund_id>`를
기록하고 이론항 없는 empirical proxy로 해석한다. 상품별 이론 exposure와 공통 시장
flow를 상품 수만큼 중복 합산하지 않는다.

### 6.4 Target·loss·탐색범위

15:20 mid가 아니라 사전 정의한 paper fill simulator의 실제 진입 가능 가격을 쓴다.
15:10 이후 처음으로 stale하지 않고 수량을 충족하는 양방향 모의 fill을 각각 계산하고,
회귀 기준가는 두 side의 실행 가능 가격 중심으로 고정한다.

```text
p_entry_ref = (p_buy_fill + p_sell_fill) / 2
y_t = official_close_price / p_entry_ref - 1
```

실제 전략 PnL은 신호 방향에 맞는 `p_buy_fill` 또는 `p_sell_fill`과 실제 spread·slippage를
사용한다. 한쪽 fill이 불가능하면 그 날 target도 결측이다. `sign(return) × auction_notional`
같은 합성값은 target이나 승격지표로 사용하지 않는다. 경매 거래량은 사후 설명 진단일 뿐이다.

학습 objective는 다음 Huber elastic-net으로 고정한다.

```text
sigma_y = 1.4826 * MAD_train(y_t)

min_(alpha,kappa,theta)
  mean Huber_(delta_multiplier * sigma_y)(y_t - y_hat_t)
  + lambda * [(1-rho)/2 * ||w||_2^2 + rho * ||w||_1]

w = concat(kappa, theta)
delta_multiplier ∈ {1.20, 1.35, 1.50}
lambda ∈ {1e-4, 1e-3, 1e-2, 1e-1, 1}
rho ∈ {0, 0.25, 0.50, 0.75, 1}
```

`sigma_y`가 0이면 적합하지 않고 `TARGET_SCALE_ZERO`로 차단한다. 규제 grid는 robust-scaled
feature에 적용한다. 60일 train 안에서 최소 20일 학습 후 10일씩 전진하는 expanding
inner fold를 만든다. 각 `(delta_multiplier, lambda, rho)` 후보는 inner-fold 평균 Huber
loss로만 비교하고, one-standard-error rule 안에서 `lambda`가 가장 크고 비영 계수가 가장
적은 후보를 고른다. **validation label·PnL·비용 후 기대값은 후보 선택에 한 번도 쓰지
않는다.** v1 `regime=single`로 고정하며 국면 분할은 새 모델 버전에서만 검토한다.

train을 한 번 더 전체 적합한 뒤 계수·scaler·hyperparameter·signal/fill/cost 설정 hash를
봉인한다. validation은 그 단일 봉인 모델의 cheap-reject 판정에 한 번만 쓰고, test를 본 뒤
범위를 바꾸면 모델 버전을 올리고 새 sealed test를 확보한다.

### 6.5 신호·사이징·paper fill 봉인

연구 배정자본은 **20,000,000 KRW**, 거래 전 목표명목은 매번 **1,000,000 KRW**로 고정한다.
수익에 따라 복리 확대하지 않는다. target 수량은 KRX 1주 단위로 다음과 같이 내림한다.

```text
k = 2.0
target_notional = 1,000,000 KRW
target_quantity = floor(target_notional / entry_limit_price)

LONG  if y_hat_gross >  k * estimated_round_trip_cost_return
SHORT if y_hat_gross < -k * estimated_round_trip_cost_return
NO_SIGNAL otherwise
```

`target_quantity<1`이면 `MINIMUM_LOT_UNMET` 무신호다. `estimated_round_trip_cost_return`은
해당 거래에 적용되는 수수료·세금·spread·slippage·시장충격과 상품비용을 모두 포함하며
누락·0이면 fail-closed다. 신호 threshold·목표명목은 validation에서 조정하지 않는다.
FR-14 risk engine은 이 고정 target에 이후 적용되어 `BLOCK/REDUCE`할 수 있고, 적용 전후
수량을 모두 보고한다.

진입 simulator `h1_continuous_fok_entry@1.0.0`은 다음으로 봉인한다.

- 대상: `000660` KRX, paper-only 단일 다리
- 주문형태: 15:10:00 이후 첫 유효 호가의 marketable limit `FOK`
- 가격: LONG은 best ask, SHORT는 best bid; 이후 reprice·추격 금지
- 수량: 위 고정 목표명목에서 계산한 전량, best 잔량이 부족하면 전량 미체결
- 입력 age: provider event 기준 `<=2s`, 양방향 10단계가 완전하고 `bid_1<ask_1`
- timeout: **15:10:02 KST**까지 첫 적격 호가가 없으면 `ENTRY_TIMEOUT`

target용 `p_buy_fill`·`p_sell_fill`도 같은 날 같은 수량·FOK 규칙으로 각각 모의한다. 둘 다
전량 체결된 날만 `p_entry_ref=(p_buy_fill+p_sell_fill)/2`와 `y_t`를 만든다. 실제 전략 PnL은
신호 방향의 fill만 사용한다. 종가 청산 simulator `h1_close_auction_outcome@1.0.0`은
15:19:30 전에 생성된 내부 paper exit intent를 official close에 대조하고, 해당 수량이
실제 종가경매 거래량의 **0.10% 이하**일 때만 전량 체결로 인정한다. 15:30:10까지
official close와 경매 거래량을 만들 수 없으면 `EXIT_OUTCOME_MISSING`으로 미체결 처리한다.

MDD 분모는 고정 초기 배정자본 **20,000,000 KRW**다. equity는 이 금액에 risk 적용 후
실현손익을 누적하며, 미체결일은 거래손익 0과 미체결 사유를 남긴다. MDD를 줄이기 위해
배정자본을 사후 변경하지 않는다.

### 6.6 Theoretical-only baseline

baseline은 동일한 `y_t`, 60/30/30 날짜, train inner fold, Huber elastic-net grid,
`regime=single`, `k=2.0`, 100만원 목표명목, FOK fill과 비용표를 사용한다. 차이는 설명변수가
`z_i`와 intercept뿐이고 `x_ofi/x_depth/x_micro/x_program/x_conflict`를 넣지 않는다는 점이다.
baseline도 L1으로 모든 `kappa`가 0이면 `THEORY_TERM_ELIMINATED_ALL`을 남긴다. proxy의
증분가치는 이 동등 조건 baseline과만 비교한다.

## 7. 결측·품질·신뢰도

### 7.1 Hard filter

다음 중 하나라도 발생하면 `value=null`, `NOT_COMPUTABLE`, 무신호다. 0 또는 직전값으로
채우지 않는다.

- `SNAPSHOT_AFTER_151000`, `POST_CUTOFF_AVAILABLE`, `PROVIDER_EVENT_TIME_MISSING`
- snapshot input age `>2s`, clock sync 오차 `>50ms`
- WebSocket disconnect, parse failure, out-of-order 또는 아래 규칙으로 탐지된 packet gap
- best quote 또는 10단계 depth 누락, `bid_1 >= ask_1`, 가격·수량 단위 미확정
- `H0STPGM0` 누락, 누적/증분·단위·reset·venue 의미 미확정
- `VI`, `HALTED`, `PRICE_LIMIT`, `MARKET_STATE_UNKNOWN`, `API_SCHEMA_DRIFT`
- 전일 NAV/AUM, beta, replication type, 20일 ADV 중 필수 구조 입력 누락
- 당일 장후 NAV/AUM 또는 15:20 이후 outcome이 feature lineage에 포함됨

`NET_CREATION_SOURCE_UNAVAILABLE`은 기본 버전에서 schema-level exclusion이며 일별 0이
아니다. 당일 creation 포함 버전은 정의하지 않는다. lagged 변형은 5.3의 T-1 입력이
인과적으로 가용하지 않으면 hard missing이다.

KIS 공개 WebSocket에 신뢰 가능한 전역 sequence가 없으면 TCP 연결상태만으로 무손실을
주장하지 않는다. 15:00~15:10의 `H0STASP0`와 병행 진단 `H0STCNT0` 각각에서 provider
event time 기준 인접 event 간격을 계산하고 **최대 허용간격을 2.0초**로 고정한다.
어느 feed든 2초를 초과하거나, `H0STCNT0` 체결이 도착한 구간에 `H0STASP0`가 2초 넘게
침묵하면 `PACKET_GAP`이다. 연결 재수립·parser error도 간격과 무관하게 hard gap이다.

### 7.2 임의 confidence 점수 폐기

Round 1의 `c_venue=0.60`, `MEDIUM>=0.50` 같은 점수·등급은 근거가 없고 삭제한다.
입력 사용 가능성은 위 hard filter로 결정한다. age, packet coverage, spread, depth,
`x_conflict`, KRX/통합 source divergence는 연속 진단값으로 그대로 저장한다.

확률 신뢰도는 다음 절차로만 만들 수 있다.

1. train의 inner-fold out-of-fold 예측으로 방향확률 후보를 생성한다.
2. 동일 train OOF 안에서 Platt와 isotonic 후보를 비교하고 one-standard-error rule로 더
   단순한 Platt를 우선한다. 양·음 outcome이 모두 없거나 적합이 식별되지 않으면
   `confidence=null / CALIBRATION_NOT_IDENTIFIED`다.
3. validation과 sealed test에서는 adaptive equal-frequency reliability curve와 Brier score를
   **사후 진단만** 하며 신호·포지션 크기·PASS 판정에 쓰지 않는다.

v1에는 신호용 confidence threshold나 등급 경계가 없다. 향후 이를 의사결정에 쓰려면
등급 숫자가 아니라 선택·검증 절차를 먼저 승인하고 새 모델 버전과 미사용 test를 확보한다.
validation 결과값에 맞춰 임의 상수를 붙인 뒤 같은 test에 적용하는 것은 금지한다.

## 8. 검증 계획과 수치 gate

### 8.1 A1 선판정과 1~2일 감사 캡처

A1은 이미 **FAIL**이다. KRX 세션 시간선과 저장소 G-03 조사에서 종가 예상체결 정보가
15:20 이후임을 확인했으므로 20일 성과 probe까지 기다리지 않는다. 1~2 KRX 거래일 동안
14:59:50~15:30:10 raw packet을 저장하는 목적은 결론을 재개방하는 것이 아니라 다음을
감사 증거로 남기는 것이다.

- 15:10 `antc_*`가 종가경매 indicative로 유효하지 않음
- 15:20 이후 `antc_*`가 갱신되는 시각과 phase
- 15:19:30 이전 의사결정과 15:20 이후 정보 사이 교집합이 없음

두 날 중 한 날이라도 15:10 값이 숫자라는 이유만으로 A1을 통과시키지 않는다. 거래소
phase가 종가 단일가 주문집합임을 공식적으로 입증해야 하는데 현재 시간선상 불가능하다.

### 8.2 2일 source/schema probe

연속장 proxy의 구현 전 read-only probe는 연속 2 KRX 거래일에 다음을 모두 만족해야 한다.

| 항목 | PASS 수치 | 실패 처리 |
| --- | --- | --- |
| 수집 구간 | 양일 14:59:50~15:30:10의 5개 TR raw 원문 보존 | `HOLD/SOURCE_PROBE_FAIL` |
| schema parse | 대상 packet parse 성공률 100%, 예상 field count 일치율 100% | schema 확정 전 구현 금지 |
| 시간 순서 | out-of-order 0건, transport disconnect 0건, client clock 오차 절대값 `<=50ms` | 당일 부적격 |
| snapshot freshness | 양일 모두 15:10:00 기준 주입력 age `<=2s` | 모델 무신호 |
| depth 완전성 | 10단계 가격·수량 완전 packet `>=99.5%`, 15:10 snapshot 100% 완전 | 미달 시 source probe 실패 |
| event 도착률 대조 | `H0STASP0`·`H0STCNT0` 각각 events/sec, p50·p95·최대 간격을 보고하고 최대 간격 `<=2.0s`; trade 도착 중 quote gap 0건 | `PACKET_GAP`, 당일 부적격 |
| 프로그램 의미 | 단위·부호·누적/증분·reset을 raw sample과 공식 schema로 100% 설명 | 한 항목 미확정이면 hard missing |
| venue | `H0STPGM0=KRX`, `H0UNPGM0=통합`, `H0NXPGM0=NXT` 식별 100% | venue 혼동 시 parser 차단 |

`H0UNPGM0`은 통합값이므로 `H0STPGM0`과 수치가 같아야 한다는 gate를 두지 않는다.
NXT 진단 피드도 같이 수집해 `통합 ≈ KRX + NXT` 관계를 검토하되, 이는
source semantics 진단일 뿐 모델 feature 합산이 아니다. exact identity와 rounding 규칙이
공식적으로 확인되지 않으면 임의 허용오차를 만들어 PASS시키지 않고
`PROGRAM_CROSSCHECK_SEMANTICS_UNRESOLVED`로 남긴다. event 도착률은 성공일만 평균내지 않고
양일 feed별 `count/600초`와 interarrival 분포 원자료를 함께 보존한다.

### 8.3 Program semantics 5단계 봉인

`H0STPGM0`은 아래 다섯 단계를 순서대로 모두 통과해야 `PROGRAM_SEMANTICS_SEALED`다.

1. **일중 단조성:** KRX 누적 매수·매도 수량/대금(`SHNU_*`, `SELN_*`)이 session 안에서
   감소하는 event가 0건이어야 한다. 순매수 `NTBY_*` 자체는 차이값이므로 단조성을
   요구하지 않는다.
2. **항등식:** 모든 유효 event에서 단위 변환 후
   `NTBY_CNQN=SHNU_CNQN-SELN_CNQN`,
   `NTBY_TR_PBMN=SHNU_TR_PBMN-SELN_TR_PBMN`이 raw 최소단위까지 정확히 맞아야 한다.
3. **EOD 3원 대사:** `H0STPGM0` 마지막 누계, KIS REST
   `investor-program-trade-today`, KRX 공식 EOD 종목별 program 결과를 같은 거래일·종목으로
   대조한다. 단위 매핑 후 수량·대금 차이는 각각 raw 최소단위 1 이하여야 하며 한 source라도
   없으면 `HOLD`다.
4. **session reset:** 공유 14:59:50 수집과 별도로 08:59:50부터 시작하는 read-only
   session-open probe를 연속 2거래일 실행한다. 당일 첫 event가 전일 마지막 누계를
   이어받지 않는지 확인하고, 설명되지 않은 장중 reset은 0건이어야 한다.
5. **통합 대사 진단:** 2초 이내 latest-as-of로 맞춘 `H0UNPGM0`, `H0STPGM0`,
   `H0NXPGM0`에 대해 `UN ≈ KRX + NXT`를 검사한다. 정규화 KRW 잔차가
   `max(1 KRW, 1bp * (abs(KRX)+abs(NXT)))` 이내인 event가 99% 이상이고 EOD 잔차도
   이 범위여야 한다.

5단계 합은 feature 생성이 아닌 venue/source 진단이다. 통합·NXT 값을 KRX 주 feature에
더하지 않는다. 어느 단계든 실패하면 단위나 reset 규칙을 사후 보정해 같은 버전을
PASS시키지 않고 `PROGRAM_CROSSCHECK_SEMANTICS_UNRESOLVED`로 차단한다.

### 8.4 20일 운영 적격성 probe

성과 검증 전 20 KRX 예정 거래일을 연속 수집한다.

- 적격일 `>=19/20`이고, 모든 적격일의 10분 window transport disconnect·parse failure가 0
- 모든 적격일의 15:10 snapshot age `<=2s` 및 10단계 완전성 100%
- 모든 적격일의 `H0STASP0`·`H0STCNT0` 최대 event 간격 `<=2.0s`와 event-rate 보고 완전성 100%
- 원시 packet과 normalized record의 record-count·hash reconciliation 100%
- KRX program window가 모든 적격일에 계산 가능하고 장중 reset 미설명 사례 0
- 실패일을 삭제하거나 성공일로 대체하지 않고 예정 거래일 분모를 보존

미달이면 성과 백테스트로 넘어가지 않고 `HOLD/DATA_QUALITY`다. 19/20은 confidence 등급이
아니라 재현 가능한 연속 window 확보를 위한 운영 적격성 gate다.

### 8.5 값싼 기각과 walk-forward

1. **source gate:** 5절과 8.1~8.3을 먼저 통과한다.
2. **train-only 선택:** 최초 60 train의 expanding inner fold에서만 scaler, Huber grid와
   calibration 후보를 선택하고 전체 train에 재적합·hash 봉인한다. `regime=single`,
   `k=2.0`, 목표명목·fill은 고정값이라 선택 대상이 아니다.
3. **cheap reject:** 다음 30일 validation은 봉인된 full proxy와 theoretical-only를
   단 한 번 순수 판정한다.
   theoretical-only 대비 OFI+program의 validation 증분 비용 후 기대값이 `<=0`이거나,
   validation 날짜 permutation `p>=0.10`이면 구현 확장을 멈추고 `REJECT`한다.
   permutation은 이미 고정된 두 모델의 일별 paired 증분 PnL label만 섞으며 grid 탐색이나
   threshold 선택을 다시 하지 않는다. 선택이 train에만 닫혀 있으므로 validation을
   재사용하지 않는다.
4. **sealed test:** cheap reject를 통과한 경우에만 다음 30 적격 거래일을 봉인된 test로
   수집해 최초 120일을 60/30/30으로 완성한다.
5. 이후 60일 이상 초기 train을 유지하고 20~30일 평가 block의 expanding
   walk-forward를 적용한다. test 조회 후 feature·window·loss를 바꾸지 않는다.

`p<0.10`은 cheap screen일 뿐 PASS 기준이 아니다. 여러 window를 훑어 가장 좋은 것을
고르지 않으며 주 window는 15:00~15:10으로 고정한다. 대안 window는 새 버전이다.

### 8.6 비용·체결·보고·최종 판정

paper fill은 15:10 이후 실제 bid/ask와 depth만 사용한다. 수수료·세금·spread·slippage,
시장충격, 거래 상품에 해당하는 보수·추적오차 등 필수 비용의 누락·0·음수는 fail-closed다.
기본 비용과 모든 필수 비용을 2배로 한 stress를 같은 실행 규칙으로 계산한다.

train/validation/test와 각 walk-forward fold마다 다음 데이터 진단을 의무 보고한다.

- 예정 거래일, `y_t` 계산 가능일·결측일·결측률과 결측 사유별 건수
- `y_t`의 평균·표준편차·MAD·최솟값·최댓값과 p01/p05/p25/p50/p75/p95/p99
- 진입 후 종가수익의 실현변동성, skew, excess kurtosis와 VI/가격제한폭 제외 건수
- `kappa_i=0`의 `THEORY_TERM_ELIMINATED:<fund_id>` 및 전체 이론항 소거 여부
- 의도 명목·risk 적용 전후 수량·fill/miss 비율·실제 평균 명목

결측일을 0 수익으로 target 학습에 넣지 않는다. 성과 calendar에는 무거래일로 남기며,
결측률 분모는 성공일이 아닌 전체 예정 거래일이다.

`PASS`는 120 적격일, sealed test 및 walk-forward, 적격 신호 30건 이상에서 다음을 모두
만족할 때 continuous proxy 연구 scope에만 부여한다.

- 비용 후 거래당 기대값 `>0`
- PF `>=1.2`
- 고정 배정자본 20,000,000 KRW 분모의 MDD `<=5%`
- 총 양의 손익 중 단일 거래일 기여도 `<=30%`
- 2배 비용 stress 누적 PnL `>=0`
- 날짜 block-bootstrap 기대값 95% CI 하한 `>0`
- 날짜 permutation `p<0.05`
- theoretical-only 대비 OFI+program의 sealed-test 증분 비용 후 PnL `>0`

표본·source·calibration이 부족하거나 CI가 넓어 결론이 안 나면 `HOLD`다. 충분한 표본에서
한 항목이라도 실패하거나 OFI 증분가치가 없으면 `REJECT`다. 결과가 양수여도 원 H1로
승격하거나 daily-proxy와 합산하지 않는다.

## 9. 핵심 가정과 반증조건

| ID | 가정 | 반증조건·조치 |
| --- | --- | --- |
| A1 | 15:10 `antc_*`가 종가경매 주문집합을 반영한다. | **이미 반증.** 예상체결 기반 v1 구조적 종료 |
| A2 | 15:00~15:10 KRX OFI가 진입 후 종가수익에 증분 정보를 가진다. | validation 증분 기대값 `<=0` 또는 permutation `p>=0.10`이면 cheap `REJECT` |
| A3 | `H0STPGM0`의 10분 KRX program flow가 종가 방향에 정보를 가진다. | semantics 불명은 `HOLD`; 학습 외 증분가치 없음·계수 반복 반전은 `REJECT` |
| A4 | 전일 NAV/AUM과 복제방식으로 만든 이론 exposure가 당일 인과적이다. | 장후 수정값 의존·lineage 부재면 해당 결과 무효 |
| A5 | 설정·환매 제외 bias가 OFI proxy 연구를 완전히 지배하지 않는다. | creation 관련일/상품에서 잔차·손익이 집중되면 기본 버전 `REJECT` |
| A6 | 실제 진입 가능 가격 이후에도 정보가 남는다. | 15:10 mid 기준만 양수이고 paper fill·비용 후 소멸하면 `REJECT` |
| A7 | 계수와 효과가 시간·regime에 안정적이다. | fold별 부호 반복 반전 또는 소수 1~3일 지배 시 `REJECT` |
| A8 | 연속장 OFI 연구의 정보가 비용보다 크다. | PRD 지표 또는 2배 비용 stress 실패 시 `REJECT` |

A1·Round 1의 crossed-book 문제는 Round 3 미해결 쟁점이 아니다. 둘 다 종료 판정이다.

## 10. 구현 수용 기준

### 10.1 이번 공유 raw 수집기

- 14:59:50~15:30:10의 원시 KIS packet을 append-only로 저장하고 TR ID, schema hash,
  provider event time, received time, venue, record ID를 역추적한다.
- `H0STASP0`, `H0STPGM0`, `H0UNPGM0`과 진단용 `H0NXPGM0`, `H0STCNT0`만 read-only
  allowlist로 구독하며 계좌·주문 endpoint를 조립하지 않는다.
- 15:20~15:30 `H0STASP0`는 일반 호가와 다른 `krx_close_indicative` dataset·record class로
  저장하되 raw `ANTC_CNPR`, `ANTC_CNQN`, `ANTC_VOL`을 가공 없이 보존한다.
- `H0STPGM0`을 주 program feed, `H0UNPGM0`을 진단 feed로 강제하고 venue 합산을 막는
  contract test가 있다. 통합·NXT는 진단 외 feature 입력이 아니다.
- paper broker나 실제 주문·계좌 endpoint를 조립하지 않는다.

### 10.2 다음 OFI feature·회귀·replay 단계

- Cont–Kukanov–Stoikov event-level OFI의 가격상승·하락·동일가격 queue 변화 단위테스트가 있다.
- 15:10:00 event cutoff, 15:19:30 intent cutoff, 2초 freshness, 15:20 이후 outcome의
  feature 유입 차단 테스트가 있다.
- 당일 설정·환매 제외가 숫자 0이 아닌 schema 상태로 보존되고, T-1 lagged creation 변형과
  experiment namespace가 분리된다.
- target은 실제 실행 가능 진입기준가를 사용하고 15:20 mid나 합성
  `sign × notional`로 되돌아가지 않는다.
- loss·hyperparameter·split·seed·비용표·data snapshot을 봉인하고 test 후 mutation을
  탐지한다.

## 11. 문서 정정 등록

현재 [`G-03 investigation`](../decisions/gates/evidence/G-03-investigation.md)의 프로그램
실시간 피드 설명은 `H0NXPGM0`을 일반 프로그램매매 피드처럼 적어 venue를 오염시킨다.
정확한 정정안은 다음과 같다.

- KRX 주피드: `H0STPGM0`
- 통합 교차검증: `H0UNPGM0`
- NXT 전용: `H0NXPGM0`
- 통합·KRX·NXT 값을 feature로 합산하지 않음

또한 같은 조사 문서가 `H0STASP0` 예상체결 수신을 15:20~15:30으로 적은 사실을 A1
반증 근거에 연결해야 한다. 이 파일은 다른 에이전트의 병렬 작업 범위이므로 이번에도
수정하지 않았다. 후속 정정이 완료될 때까지 이 절이 명시적 correction backlog다.

## 12. 근거

### 저장소

- [`prd.md`](../../prd.md) 7.1: 무료 피드만 사용하며 미지원 필드를 유료로 자동 보충하지 않음
- [`prd.md`](../../prd.md) 9.1: 15:10 snapshot, 15:19:30 intent 마감, 결측 0 대체 금지,
  별도 모델 버전, 장후 NAV/AUM 룩어헤드 차단
- [`G-03 investigation`](../decisions/gates/evidence/G-03-investigation.md): `H0STASP0`
  예상체결 수신 구간 15:20~15:30 및 원천 imbalance 부재

### 외부 1차 근거

- [KRX ETF 거래 안내](https://global.krx.co.kr/contents/GLB/06/0605/0605010102/GLB0605010102.jsp):
  연속장 종료 및 종가 단일가 구간을 각각 15:20, 15:20~15:30으로 명시한다.
- [Cont, Kukanov, Stoikov, *The Price Impact of Order Book Events*](https://arxiv.org/abs/1011.6402):
  best bid/ask queue event로 OFI를 정의하고, 짧은 구간 가격변화와의 선형 관계 및 depth와
  impact의 역관계를 분석한 원 논문. KRX 종가 예측으로의 전이는 이 설계가 별도 검증한다.
- KIS 공식 `open-trading-api` commit
  `885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc`의
  [KRX 호가 `H0STASP0`](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/asking_price_krx/asking_price_krx.py),
  [KRX program `H0STPGM0`](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/program_trade_krx/program_trade_krx.py),
  [통합 program `H0UNPGM0`](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/program_trade_total/program_trade_total.py),
  [NXT program `H0NXPGM0`](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/program_trade_nxt/program_trade_nxt.py)

KIS 예제는 TR 신분과 schema 근거다. 현 계정의 구독권한, 실제 지연, 누적/증분 의미,
15:10 packet 품질은 8절 prod read-only probe 전까지 미확정이다.

## 13. Round 3 봉인 결과와 남은 외부 의존성

Round 3에서 병행 플랜, train-only model selection, 신호·사이징·fill, lagged creation,
program 5단계 대사와 confidence 진단-only 정책을 모두 확정했다. 같은 v1에서 수치를
바꾸지 않는다.

남은 항목은 설계 질문이 아니라 외부 증거·권한 의존성이다.

- 현 KIS 계정과 실제 장중 raw로 8.1~8.4 source gate를 통과할 것
- KIS REST와 KRX EOD program 자료로 5단계 semantics를 실측 봉인할 것
- OFI feature·회귀·paper replay는 다음 구현 단계에서 이 문서 hash를 입력으로 사용할 것
- 실제 종가경매 참여 모델은 별도 PRD 9.1 개정 승인이 있기 전까지 `BLOCKED`
- G-03 investigation의 venue 정정은 병렬 문서 작업 완료 후 확인할 것

## 14. 공유 raw 수집기와 라이브 실행 경계

공유 수집기는 `000660`만 허용하고, KST 거래일별 14:59:50~15:30:10에 다음을 저장한다.

| TR ID | venue·역할 | dataset |
| --- | --- | --- |
| `H0STASP0` 15:20 이전 | KRX 연속장 10단계 호가 | `h1_krx_orderbook_raw_v1` |
| `H0STASP0` 15:20~15:30 | KRX 종가경매 호가·indicative | `h1_krx_close_indicative_raw_v1` |
| `H0STPGM0` | KRX program 주원천 | `h1_krx_program_raw_v1` |
| `H0UNPGM0` | KRX+NXT 통합 program 진단 | `h1_integrated_program_raw_v1` |
| `H0NXPGM0` | NXT program 통합대사 진단 | `h1_nxt_program_diagnostic_raw_v1` |
| `H0STCNT0` | KRX 체결·packet gap 진단 | `h1_krx_trade_diagnostic_raw_v1` |

각 gzip JSON raw envelope에는 원문 field/value, ordered schema와 SHA-256 schema hash,
TR ID, symbol, provider event KST/UTC, client received UTC, logical venue, record class,
close-indicative 여부가 들어간다. PostgreSQL raw catalog의 UUID가 lineage parent이고,
catalog checksum·provider catalog version·collection run ID와 payload 경로를 결합한다.
동일 packet은 멱등 skip하고 같은 event의 다른 payload는 별도 checksum key로 보존한다.

sanitized fixture 검증은 네트워크·키 없이 수행한다. 실제 라이브 실행은 별도 runbook
단계로 다음을 요구한다.

1. 사용자 read-only KIS app key/secret과 WebSocket approval key를 secret provider로 주입
2. NTP clock 오차 `<=50ms`, 테스트 전용이 아닌 append-only 운영 DB·data root 확인
3. 14:59:50 이전 5개 read-only TR 구독과 schema hash 확인
4. 15:30:10 종료 후 feed별 count·interarrival·checksum·close-indicative record 수 대사
5. 주문·계좌 TR 또는 broker registry가 조립되지 않았음을 실행 manifest에 기록

라이브 장중 캡처를 아직 수행하지 않았다는 사실은 `HOLD/LIVE_SOURCE_NOT_CAPTURED`로
남긴다. fixture 통과를 실제 feed 가용성이나 program semantics PASS로 승격하지 않는다.
