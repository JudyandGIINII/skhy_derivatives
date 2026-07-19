# G-04 증거: H1 실제 최소 필드와 무료 KRX Open API 충족 범위

## 판정 요약

- 분석 기준: 2026-07-18, `feature/phase0-1-implementation`
- 사용자 제약: KRX 데이터는 현재 승인된 무료 Open API 범위만 사용한다. 유료 데이터와
  데이터 구매는 대안으로 두지 않는다.
- 원래 H1의 핵심 계산 입력은 `beta`, 총 노출 규모인 `prior_nav`, 신호 시점까지의
  기초자산 수익률, 상품·국면별 `kappa`, 선택적 flow 보정, 기초자산 20일 ADV다.
- 현재 H1 코드가 PCF, 구성종목, 복제방식, 장중 iNAV를 직접 읽지는 않는다.
  `FundSnapshot`에서도 전략 로직이 직접 읽는 값은 `fund_id`와 `published_at`뿐이다.
- 무료 KRX 일별정보로 상품 식별, 기준일, 종가, ETF NAV/ETN IV, 상장좌수는 확보된다.
  그러나 실제 AUM은 총액 필드가 0이고, 15:10 장중 수익률과 검증 가능한
  `published_at`은 없다. 따라서 **원래의 15:10 H1은 미충족**이다.
- 다만 `NAV 또는 IV × LIST_SHRS`를 실제 AUM이 아닌 `listed_notional_proxy`로 쓰고,
  일별 종가 기반 연구로 시간 해상도를 낮춘 **무료 KRX 축소모델**은 가능하다. 이 모델은
  원래 H1 및 완전모델과 별도 버전·별도 성과로 관리해야 한다.
- 이 최소조건은 `GATE_DEFINITIONS["G-04"]`와 `docs/decisions/gates/G-04.md`에 채택됐다.
  G-04의 `CONFIRMED`는 무료 KRX 일별 proxy 계약만 확인하며 원래 15:10 H1이나 실시간
  KIS/Toss feed의 준비 완료를 뜻하지 않는다.

## 1. 배경·요구 문서가 말하는 H1

요구 문서는 처음에는 AUM과 장중 수익률로 종가 기계매매를 역산하는 문제로 시작했으나,
최종 PRD와 구현계획에서 실제 헤지 전이와 룩어헤드 통제를 추가했다.

| 근거 | H1 데이터 요구 해석 |
| --- | --- |
| `background.md` 34~50행 | 레버리지 상품이 상승일에는 종가 부근 매수, 하락일에는 종가 부근 매도를 한다는 메커니즘과 상품 순자산 규모를 가설 배경으로 둔다. 제시된 규모 수치는 원자료 검증 전 주장이지 코드 입력 계약은 아니다. |
| `draft_prd.md` 17~20, 41~48행 | 당일 장중 등락률과 레버리지 ETF AUM으로 종가 기계매매량을 역산하고 장 마감 10~20분 전에 진입하라고 요구한다. |
| `prd.md` 136~141행 | AUM은 확정 주문량이 아니라 잠재 수급의 상한 또는 설명변수이며, 실제 헤지는 상품 구조·상계에 영향을 받는다고 제한한다. |
| `prd.md` 266~277행 | 광의 `FundSnapshot` 계약은 AUM, NAV, iNAV, 발행좌수, 설정·환매, 복제방식, `published_at`, `effective_at`을 정의한다. |
| `prd.md` 310~339행 | 이론 노출은 `beta * (beta - 1) * prior_nav * underlying_return`; 최종 압력은 상품·국면별 `kappa`, flow 보정, 20일 ADV를 사용한다. 신호는 15:10이며 전일까지 공개된 AUM/NAV만 허용한다. |
| `implementation_plan.md` 241, 341~347행 | AUM/NAV와 flow의 as-of join, 상품·국면·버전별 `kappa`, 예상체결/프로그램 피드 결측 시 별도 축소모델을 요구한다. P1-02 수집 범위는 iNAV·PCF·복제방식까지 넓게 잡혀 있다. |

여기서 `prior_nav`는 이름만 보면 1좌당 NAV로 오해할 수 있으나, 코드 수식의 결과를
원화 노출액으로 만들고 이를 원화 ADV로 나누려면 **상품 전체의 직전 노출 규모**여야 한다.
따라서 본 문서에서는 `prior_nav`를 “직전 시점의 총 순자산/AUM 성격의 원화 notional”로
판정한다. 1좌당 ETF NAV 또는 ETN IV만 넣으면 상품 크기가 사라지고 단위도 맞지 않는다.

## 2. 실제 코드가 소비하는 필드

### 2.1 수치·시점 판정에 직접 쓰는 필드

| 단계 | 실제 소비 필드 | 코드상 출처(파일:심볼) | 용도 |
| --- | --- | --- | --- |
| 이론 노출 | `beta` | `src/skhy_research/features/h1_close_pressure/theoretical_exposure.py:theoretical_delta_exposure` | `beta * (beta - 1)` 계수 |
| 이론 노출 | `prior_nav` | 같은 심볼 | 상품 전체 직전 노출 규모 |
| 이론 노출 | `underlying_return` | 같은 심볼 | 신호 시점까지의 기초자산 수익률 |
| kappa 저장·조회 | `fund_id`, `regime`, `kappa`, `version` | `src/skhy_research/features/h1_close_pressure/kappa_registry.py:KappaRegistry.set_kappa`, `KappaRegistry.get_kappa` | 상품·국면·전략 버전별 학습 계수 식별과 저장 |
| 상품별 압력 | `fund_id`, `theoretical_delta_exposure`, `kappa`, `observable_flow_adjustment` | `src/skhy_research/features/h1_close_pressure/close_pressure.py:FundContribution` | 상품별 이론 노출에 실제 전이계수와 관측 flow를 반영 |
| ADV 정규화 | `underlying_20d_adv_notional` | `src/skhy_research/features/h1_close_pressure/close_pressure.py:estimated_close_pressure` | 합산 압력을 기초자산 20일 평균 거래대금으로 정규화 |
| 의사결정 시각 | `trading_date`, `signal_snapshot_time_kst`, `order_intent_cutoff_kst` | `src/skhy_research/strategies/h1_close_rebalance/decision_window.py:build_decision_window` | 15:10 snapshot과 15:19:30 cutoff를 UTC ns로 변환 |
| 룩어헤드 | `fund_snapshots[*].published_at`, `fund_snapshots[*].fund_id`, `decision_time_utc` | `src/skhy_research/strategies/h1_close_rebalance/lookahead_guard.py:assert_no_lookahead` | `published_at >= decision_time_utc`인 snapshot을 거부하고 위반 상품을 식별 |
| 신호 크기·방향 | `close_pressure.value` | `src/skhy_research/strategies/h1_close_rebalance/strategy.py:H1CloseRebalanceStrategy.decide` | neutral band 비교, LONG/SHORT 방향, 기대 총수익·confidence 계산 |
| 모델 품질 설명 | `close_pressure.model_version`, `close_pressure.missing_flow_fund_ids` | 같은 심볼 | full/reduced와 flow 결측 상품을 설명 정보에 보존 |
| 임계값·비용 | `neutral_band`, `estimated_cost` | `src/skhy_research/strategies/h1_close_rebalance/strategy.py:H1CloseRebalanceStrategy.__init__`, `decide` | 무신호 구간과 기대 순수익 계산 |

`observable_flow_adjustment=None`이면 수치 합산에서는 그 항을 0으로 두지만,
`estimated_close_pressure`가 `model_version="reduced"`로 내리고 해당 `fund_id`를
`missing_flow_fund_ids`에 남긴다. 즉 0으로 조용히 완전모델을 가장하지 않는다.

반대로 `kappa`는 `FundContribution`에서 결측을 허용하지 않는다. `KappaRegistry`는 미등록
key에 `None`을 반환하지만, `estimated_close_pressure`에는 이를 처리하는 기본값이 없다.
따라서 `kappa=1` 같은 임의 기본값은 현재 코드 계약에도 맞지 않으며, 별도 학습값 또는
명시적인 다른 축소모델 정의가 필요하다.

### 2.2 내부 설정·lineage 필드

다음 값도 전략 메서드의 필수 인자이지만 KRX에서 수집할 시장 데이터는 아니다.

| 필드 | 코드상 출처(파일:심볼) | 성격 |
| --- | --- | --- |
| `instrument_id`, `feature_set_id`, `input_record_ids` | `strategy.py:H1CloseRebalanceStrategy.decide` | 거래대상·feature·원천 lineage 식별자 |
| `decision_time_utc`, `expires_at_utc`, `signal_id` | 같은 심볼 | 신호 생성·만료·고유 식별 메타데이터 |
| `strategy_version` | `strategy.py:H1CloseRebalanceStrategy.__init__` | 전략 버전 |
| `regime` | `kappa_registry.py:KappaRegistry` | 연구자가 정의하는 국면 label |
| `signal_snapshot_time_kst`, `order_intent_cutoff_kst`, `neutral_band`, `estimated_cost` | `decision_window.py:build_decision_window`, `strategy.py:H1CloseRebalanceStrategy` | 설정 또는 비용모델 산출물 |

### 2.3 `FundSnapshot` 타입과 실제 읽기 사이의 차이

`strategy.decide`는 `list[FundSnapshot]`을 받으므로 현재 도메인 객체를 정상 생성하려면
`src/skhy_research/domain/reference.py:FundSnapshot`이 요구하는 `leverage_beta`, `aum`,
`nav`, `replication_type`, `published_at`, `effective_at` 및 상속 envelope 필드도 필요하다.
그러나 지정된 6개 H1 파일의 실행 로직이 snapshot에서 직접 읽는 것은 `fund_id`와
`published_at`뿐이다. `aum`, `nav`, `inav`, `shares_outstanding`,
`net_creation_estimate`, `replication_type`, `effective_at`은 H1 전략에서 읽지 않는다.

`src/skhy_research/application/h1_krx_daily_proxy.py`는 2026-07-18부터 KRX universe 결과를
기존 `theoretical_delta_exposure`와 `estimated_close_pressure`에 연결한다. 이 경로는
`FundSnapshot`을 실제 AUM/NAV snapshot으로 위장하지 않고 별도 daily-proxy 입력 계약을
사용한다. `tests/e2e/test_h1_pipeline_end_to_end.py`의 합성 full 경로와도 별도다.

## 3. 무료 KRX Open API 충족 대조

판정 기준은 다음과 같다.

- `충족`: 무료 API 원필드 또는 손실 없는 내부 계산으로 현재 의미를 만족한다.
- `부분충족`: 날짜 해상도, 이름 기반 추론 또는 명시적 proxy로만 만족한다.
- `미충족`: 필드가 없거나 실측값이 사용할 수 없고, 원 H1의 의미를 유지한 대체도 없다.

| H1 필요 필드·근거 | 무료 KRX 매핑 | 판정 | 해석 |
| --- | --- | --- | --- |
| `fund_id` | `ISU_CD`로 안정 내부 ID 생성 | **충족** | `application/leverage_universe_discovery.py:discover_and_register_krx_leveraged_universe`가 구현한다. |
| `trading_date` | `BAS_DD` | **충족** | 날짜 수준 기준일이다. 시각이나 timezone 증거는 아니다. |
| 상품 종류와 기초자산 연결 | ETF/ETN endpoint 구분, `ISU_NM`, `IDX_IND_NM` | **충족** | 기준일에 응답에 존재한 국내 단일종목 상품과 SK하이닉스 기초 연결이 가능하다. 공식 상장상태 이력은 아니다. |
| `beta` | `ISU_NM`의 `레버리지`, `인버스`, `nX` marker로 추론 | **부분충족** | 현재 `_parse_leverage_factor`가 +2/-1/-2를 만든다. API의 공식 목표배율 전용 필드가 아니므로 해석 실패 상품은 제외해야 한다. |
| ETF 1좌당 NAV | `NAV` | **충족** | 날짜별 값은 얻지만 총 AUM은 아니다. |
| ETN 1증권당 IV | `PER1SECU_INDIC_VAL` | **충족** | 날짜별 IV이며 장중 iIV/iNAV가 아니다. |
| 발행·상장 수량 | `LIST_SHRS` | **충족** | 무료 응답에서 얻는다. 실제 투자자 보유량 또는 실제 헤지된 수량과 같다고 보장되지는 않는다. |
| 실제 AUM/총 notional | ETF `INVSTASST_NETASST_TOTAMT`, ETN `INDIC_VAL_AMT` | **미충족** | 필드는 있으나 실측 18개 상품 모두 0이어서 사용할 수 없다. |
| `prior_nav` | `NAV × LIST_SHRS` 또는 `IV × LIST_SHRS` | **부분충족** | `listed_notional_proxy`는 만들 수 있다. ETF는 총 순자산 근사, ETN은 발행사 재고까지 포함해 실제 시장 노출을 크게 과대평가할 수 있다. 실제 AUM이라고 저장하면 안 된다. |
| 날짜별 기초가격·수익률 | `OBJ_STKPRC_IDX` 또는 일별 종가 | **부분충족** | 종가 간 일별 수익률 연구는 가능하다. 원 H1의 15:10 시점 `underlying_return`은 만들 수 없다. |
| 15:10 기초자산 수익률·호가·거래량 | 일별 endpoint에는 없음 | **미충족** | 원래 종가 선행 진입의 핵심 시점 입력이다. 일별 종가로 바꾸면 다른 해상도의 연구모델이다. |
| `underlying_20d_adv_notional` | 무료 주권 일별정보의 `000660` 거래대금 20거래일 평균 | **충족** | 현재 read-only client가 주권 일별 endpoint를 지원한다. 단, 일별 ADV이며 장중 유동성은 아니다. |
| `published_at` | 제공 없음 | **미충족** | `BAS_DD`나 HTTP 수신시각을 과거의 실제 게시시각으로 변조할 수 없다. 현재 룩어헤드 guard의 literal 계약을 충족하지 못한다. |
| `effective_at` | `BAS_DD` 날짜만 존재 | **부분충족** | 날짜 기준은 알지만 시·분·timezone 포함 기준시각은 없다. H1 계산이 직접 읽지는 않으나 현재 `FundSnapshot` 생성에는 필수다. |
| `observable_flow_adjustment` | PCF·설정환매·프로그램·예상체결 정보 없음 | **미충족** | 현재 코드가 지원하는 G-03식 reduced 처리 대상이다. |
| `kappa` | API 필드가 아니라 학습 산출물 | **부분충족** | 허용 데이터로 별도 추정·버전 관리할 수 있으나 현재 estimator가 없고, 일별 종가만으로는 원래 종가경매 전이계수를 식별하기 어렵다. |
| `regime` | 내부 label | **충족** | 고정 단일 regime 또는 무료 데이터로 판정 가능한 universe 기간을 정의할 수 있다. 외부 유료 필드가 필요하지 않다. |
| PCF·구성종목 | 제공 없음 | **미충족** | 현재 6개 H1 파일의 직접 입력은 아니다. 완전 flow/구조 설명에는 필요하다. |
| 복제방식 | 제공 없음 | **미충족** | `IDX_IND_NM`의 `선물`은 기초지수 단서이지 실제 복제방식 증거가 아니다. 현재 6개 H1 파일은 이를 직접 읽지 않는다. |
| 장중 iNAV/iIV | 제공 없음 | **미충족** | 현재 이론식과 전략이 직접 읽지 않으므로 무료 일별 축소모델에는 비필수다. |
| 종가·상품 거래대금 | `TDD_CLSPRC`, `ACC_TRDVAL` | **충족** | 일별 연구·사후 대조에는 사용 가능하지만 종가경매 자체의 거래대금이나 불균형은 아니다. |
| `neutral_band`, 비용, ID·lineage·버전 | 내부 설정·저장소에서 생성 | **충족** | KRX 데이터 구매와 무관하다. 비용은 가정과 스트레스 시나리오임을 명시해야 한다. |

종합하면 무료 KRX API는 **방향과 상대적 규모를 탐색하는 일별 축소 feature**에는 충분한
원재료를 제공하지만, 원래 H1의 15:10 의사결정과 정확한 과거 as-of 재현에는 충분하지
않다. 특히 종가가 있다는 사실은 종가 10~20분 전 가격이 있다는 뜻이 아니다.

## 4. 미충족 필드의 치명도와 축소모델 우회

| 미충족·부분 필드 | 원 H1 치명도 | G-03식 축소 우회 | 조건과 한계 |
| --- | --- | --- | --- |
| 실제 AUM / `prior_nav` | 높음 | **가능** | `NAV/IV × LIST_SHRS`를 `listed_notional_proxy`로 사용한다. 방향은 유지되지만 상품별 크기와 압력 magnitude가 왜곡된다. 실제 AUM과 혼용하지 않는다. |
| 공식 `beta` | 중간~높음 | **가능** | 이름 기반 배율을 쓰되 규칙·confidence·exclusion을 저장한다. 해석 불가 상품은 0이나 +2로 기본처리하지 않는다. |
| `published_at` | 원 H1과 현재 guard에는 치명적 | **조건부** | forward 수집에서는 실제 `received_time_utc < decision_time`을 가용성 증거로 쓸 수 있다. 과거 backfill에는 당시 게시시각이 증명되지 않으므로 검증된 보수적 lag가 생기기 전까지 ex-post 연구로만 표시한다. `BAS_DD`를 임의 timestamp로 만들지 않는다. |
| 15:10 `underlying_return`·호가 | **치명적** | 원 H1 의미를 유지한 우회는 **불가** | 일별 종가 수익률로 대체하면 “종가 10~20분 전 선행 진입”이 아니라 일별 proxy 연구가 된다. 별도 모델 ID가 필수다. |
| `observable_flow_adjustment` | 완전모델에는 높음 | **가능, 이미 구현** | `None`을 보존하고 `model_version="reduced"`, 결측 상품 ID를 기록한다. 완전모델 성과와 합산하지 않는다. |
| `kappa`와 종가경매 calibration target | 높음 | **조건부** | 학습 구간에서만 pooled/product별 계수를 추정할 수 있다. 무료 일별 target을 쓰면 원래 종가경매 전이계수가 아니라 다른 계수이므로 별도 버전이어야 한다. `kappa=1` 기본값은 금지한다. |
| PCF·복제방식 | 완전 flow 해석에는 높음, 이론-only 계산에는 낮음 | **가능** | 무료 축소모델에서는 직접 제외하고, 미관측 실제 헤지 전이를 학습된 `kappa`의 오차에 포함시킨다. 단, 충분한 target 없이 구조를 “흡수했다”고 주장할 수 없다. |
| 장중 iNAV/iIV | 낮음 | **가능** | 현재 코드의 직접 입력이 아니므로 축소모델 최소조건에서 제외한다. |
| 시·분 단위 `effective_at` | 현재 `FundSnapshot` 구조에는 높음, 이론식에는 낮음 | **조건부** | 날짜 기준과 실제 수신시각을 분리 보존한다. 정확한 기준시각을 만들어내지 않는다. 현 타입 계약과의 불일치는 향후 별도 설계 대상이다. |

G-03의 reduced 패턴을 그대로 적용할 수 있는 것은 flow 결측이다. AUM proxy, 일별
수익률, 불명확한 게시시각, 대체 `kappa`까지 함께 쓰려면 단순히 기존
`model_version="reduced"`라고만 부르면 차이를 식별할 수 없다. 최소한
`h1_krx_daily_proxy_reduced_v1`처럼 데이터 해상도와 proxy를 드러내는 별도 모델 ID가
필요하다.

## 5. 채택된 무료 API 전용 G-04 최소 요구조건

### 5.1 축소된 게이트 질문

종전 G-04는 국내 단일종목 레버리지 상품 전체에 대해 PCF·AUM/NAV 공개시각과
복제방식을 요구했다. 무료 KRX만 사용한다는 사용자 방침과 실제 H1 코드의 직접 소비
범위를 반영해 다음 질문으로 좁혔다.

> **G-04 질문:** 무료 KRX ETF/ETN 일별정보만으로 H1에 포함할 국내 SK하이닉스
> 단일종목 레버리지 상품을 거래일별로 재현하고, 배율과 직전 listed-notional proxy를
> 산출하며, 그 값이 의사결정 시점에 사용 가능했음을 허위 timestamp 없이 판정할 수
> 있는가?

이 질문은 KRX 일별 과거 확인·크로스체크·백필 범위만 판정한다.

### 5.2 acceptance criteria

1. **유니버스**: 무료 `/svc/apis/etp/etf_bydd_trd`와
   `/svc/apis/etp/etn_bydd_trd`에서 `BAS_DD`, `ISU_CD`, `ISU_NM`, `IDX_IND_NM`을
   보존하고, SK하이닉스 연결과 ETF/ETN 구분을 거래일별로 재현한다.
2. **배율**: 상품명 marker로 `beta`를 해석한 규칙 버전과 원문을 저장한다. 해석 실패,
   기초자산 불명확, 중복 상품은 exclusion과 사유를 남긴다.
3. **규모 proxy**: ETF는 `NAV × LIST_SHRS`, ETN은
   `PER1SECU_INDIC_VAL × LIST_SHRS`를 `listed_notional_proxy`로 계산한다. 원필드가
   null/0/음수이면 제외하며, 이를 `aum` 또는 실제 헤지 notional이라고 명명하지 않는다.
   총액 필드의 0은 결측으로 처리한다.
4. **시점 안전성**: 원천 `BAS_DD`, 실제 수집 `received_time_utc`, 적용 decision date를
   모두 보존한다. forward 신호에는 decision 전 실제 수신된 직전 기준일 값만 허용한다.
   historical backfill은 당시 가용성을 입증하는 보수적 lag가 검증되기 전까지 ex-post
   연구로 표기하며, 가짜 `published_at`/`effective_at`을 만들지 않는다.
5. **기초 수익률·ADV**: 무료 일별 종가와 `000660` 일별 거래대금으로 일별 수익률 및
   20일 ADV를 만든다. 이 입력은 15:10 신호가 아닌 일별 연구 해상도임을 계약에 고정한다.
6. **축소모델 표기**: flow는 missing으로 보존하고, PCF·복제방식·iNAV·실제 AUM을 쓰지
   않았음을 manifest와 결과에 기록한다. 최소 모델 ID는
   `h1_krx_daily_proxy_reduced_v1`처럼 완전모델과 구분한다.
7. **kappa**: 허용 데이터의 train 구간에서만 추정하고 `(fund_id, regime,
   strategy_version)`과 함께 저장한다. 추정할 target이나 표본이 부족하면 해당 상품을
   제외한다. 1 또는 임의값을 기본값으로 쓰지 않는다.
8. **lineage·품질**: 사용한 원천 record ID, 산식 버전, exclusion, missing flag, proxy
   여부를 결과까지 추적한다. 무료 API 외 데이터를 조용히 섞지 않는다.

### 5.3 구현·검증 증거

| 계약 | 구현 | 검증 |
| --- | --- | --- |
| 이름 기반 beta와 SK하이닉스 universe | `application/leverage_universe_discovery.py` | `tests/unit/test_leverage_universe_discovery.py`; 실제 2026-07-16 응답의 18개 universe는 `G-04-universe-probe.md` |
| `NAV/IV × LIST_SHRS` 규모 proxy, 일별 수익률, 20일 ADV | `application/h1_krx_daily_proxy.py:build_krx_daily_proxy_feature` | `tests/unit/test_h1_krx_daily_proxy.py`, `tests/integration/test_h1_krx_daily_proxy_pipeline.py` |
| 직전 기준일·수신시각·lineage | 같은 builder의 basis-date/as-of/record-ID 검증 | 같은 단위·통합 테스트의 미래 데이터 및 lineage 결측 차단 |
| 모델·해상도·성과 분리와 engine 호환 | `ClosePressureResult`, `H1CloseRebalanceStrategy`, 기존 `run_backtest`, `PromotionInput/Result`, `ExperimentResult` | scope mismatch 예외, fixture Signal→OrderIntent→engine 체결, proxy 강제 HOLD, proxy PASS 저장 거부 테스트 |

일별 proxy는 `h1_krx_daily_proxy_reduced_v1`, `daily-proxy`,
`h1-daily-proxy-research-only`, `promotion_eligible=False`를 feature·전략 설명·프로모션
결과에 보존한다. KRX 실제 응답 증거와 이 계약 테스트를 합치면 축소 G-04의 universe,
배율, 규모 proxy, 가용성, lineage 조건이 충족된다.

### 5.4 축소된 G-04의 비필수 항목

다음은 `h1_krx_daily_proxy_reduced_v1`의 G-04 통과조건에서는 제외할 수 있다.

- PCF·구성종목
- `PHYSICAL/FUTURES/SWAP/MIXED` 복제방식
- 장중 iNAV/iIV
- 0이 아닌 공식 AUM/지표가치총액
- NAV/IV의 시·분 단위 실제 게시시각과 timezone 포함 `effective_at`
- 국내 H1 축소 universe 밖의 모든 레버리지 상품 및 HKEX 상품

정확한 게시시각을 비필수로 돌리는 대신 acceptance criterion 4의 실제 수신시각과
직전 기준일 규칙을 강제한다. daily-proxy builder는 이를 별도 계약으로 구현하며,
원래 `FundSnapshot`/lookahead guard의 `published_at` 계약을 완화하거나 우회하지 않는다.

### 5.5 G-04가 좁아져도 남는 경계

- G-04를 위 범위로 좁혀도 무료 일별 API에는 15:10 가격·호가가 없다. 따라서 원래
  H1 페이퍼 신호는 여전히 실행할 수 없다.
- 종가경매 불균형·프로그램매매·flow는 G-03 reduced 상태로 남는다.
- 120 KRX 거래일과 60/30/30 검증은 데이터 필드 gate와 별도인 Phase 1 표본수
  완료조건이다. G-04 축소만으로 그 조건이 충족되지는 않는다.
- 무료 일별 축소모델의 결과는 원래 H1의 PASS 근거, 완전모델 성과 또는 실시간
  거래 가능성으로 승격하면 안 된다. “무료 KRX 일별 proxy 가설”의 HOLD/REJECT 연구로
  별도 판정하는 것이 안전하다.

## 결론

H1의 실질 최소 필드는 PCF 전체가 아니라 `fund_id`, `beta`, 직전 총규모,
신호 시점 기초수익률, 상품·국면별 `kappa`, 선택적 flow, 20일 ADV와 as-of 증거다.
무료 KRX API는 이 중 상품·날짜·종가·NAV/IV·상장좌수·일별 ADV를 제공하므로 총규모
proxy를 쓴 일별 축소 연구에는 사용할 수 있다. 그러나 실제 AUM, 검증 가능한
`published_at`, 15:10 수익률이 없으므로 원 H1은 충족하지 못한다.

따라서 G-04는 PCF·복제방식·iNAV를 모든 상품에 강제하는 대신, 선택된 국내 H1
universe의 배율·listed-notional proxy·가용성·lineage를 검증하는 무료 API 전용
축소 gate로 좁혔다. 모델 이름·해상도·promotion scope 분리와 원래 15:10 H1 성과 합산
금지는 코드로 강제한다.
