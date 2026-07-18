# SK하이닉스 구조적 수급·교차시장 상대가치 연구 시스템 구현 계획

| 항목 | 내용 |
| --- | --- |
| 문서 상태 | 구현 착수용 계획 v1.0 (병합 확정본) |
| 기준 문서 | `prd.md` v1.0, 정보 기준일 2026-07-18 |
| 병합 출처 | `implementation_plan_codex.md`(기준안) + `implementation_plan_agy.md`(부분 채택), 2026-07-18 사용자 결정으로 병합 |
| 적용 범위 | PRD 16장 Phase 0~3: 연구, 이벤트 기반 백테스트, 페이퍼트레이딩 |
| 제외 범위 | 실제 브로커 주문, 다중 사용자 서비스, 투자자문·신호 판매, Phase 4 실거래 검토 |
| 요구사항 표기 | `FR-01`~`FR-16`은 PRD 12장의 기능 요구사항 ID를 뜻함 |

### 병합 결정 기록

두 개의 독립 초안(Codex안, Antigravity안)의 불일치 지점에 대해 사용자가 다음과 같이 확정했다. 이 문서는 그 결정을 반영한 단일 기준 계획이며, 두 초안 원문은 참고 기록으로 보존한다.

| 결정 항목 | 확정안 | 내용 |
| --- | --- | --- |
| 운영 메타데이터 저장소 | Codex안 | PostgreSQL 16을 체크포인트·계보·주문 상태의 권위 저장소로 사용 (연구 질의는 DuckDB/Parquet) |
| G-gate 착수 순서 | Codex안 | G-06 데이터 라이선스 확인을 모든 실데이터 지속 수집보다 먼저 수행 |
| 리포트·UI | Antigravity안 | Streamlit + Plotly 대시보드로 PRD 15장 운영 화면·리포트 구현 |
| 비밀값 관리 | Codex안 | 환경변수 + macOS Keychain 어댑터, canary secret 유출 테스트 포함 |
| 작업 분해 상세도 | Codex안 | 39개 작업의 선행조건·완료 검증·FR 역참조 체계 유지 |

## 1. 목적과 구현 원칙

이 문서는 `prd.md`를 단일 진실 공급원으로 삼아 구현 순서, 모듈 경계, 데이터 흐름, 검증 게이트를 정의한다. `background.md`와 `draft_prd.md`는 문제 제기와 초기 아이디어를 이해하기 위한 읽기 전용 원문이며, 사실·수식·거래 방향·수용 기준은 `prd.md`만 따른다.

구현 전반에 다음 원칙을 적용한다.

1. **안전한 기본값**: 데이터 품질, 시장 상태, 전환, 차입, 공급자 capability가 불명확하거나 만료되면 추정하지 않고 `BLOCK`한다.
2. **시점 일관성**: 전략은 의사결정 시각까지 수신·공개된 데이터만 읽는다. `event_time_utc`와 `received_time_utc`를 함께 사용해 룩어헤드를 차단한다.
3. **불변성과 계보**: 원시 레코드는 수정하지 않는다. 정규화 이후 모든 산출물은 부모 레코드 ID, 알고리즘 버전, 설정 해시, 데이터 스냅샷 ID, 저장소 커밋을 가진다.
4. **연구·실시간 동형성**: 백테스트와 페이퍼트레이딩은 같은 이벤트, 전략, 리스크, 비용, 체결 계약을 사용하고 시계와 데이터 입력 어댑터만 바꾼다.
5. **포트 우선 설계**: 공급자 SDK 객체를 도메인과 전략에 노출하지 않는다. 외부 연동은 네 개의 공급자 포트 뒤에 격리한다.
6. **실주문 차단**: v1 의존성 주입 컨테이너에는 `PaperBrokerProvider`만 등록한다. KIS/Toss 주문 어댑터는 구현·빌드·등록하지 않는다.
7. **가설 독립성**: H1, H2, H3는 별도 플러그인, 설정, 버전, 실험 결과를 갖는다. 한 전략의 통과가 다른 전략을 승격시키지 않는다.
8. **비용 후 반증 우선**: 기본 비용과 2배 비용에서 거래 가능성을 판단하며, 표본 부족은 `HOLD`, 기준 실패는 `REJECT`로 처리한다.

## 2. 기술 스택 선정과 근거

### 2.1 선정 스택

| 영역 | 선정 기술 | 선정 근거와 적용 방침 |
| --- | --- | --- |
| 언어·런타임 | Python 3.12 | 금융 데이터, 통계, API 연동 생태계가 풍부하고 단일 사용자 연구 시스템의 개발 속도에 적합하다. `Decimal`과 정수형 UTC epoch nanoseconds를 도메인 경계에서 강제한다. |
| 패키지·환경 | `uv`, `pyproject.toml`, 고정 lockfile | 빠르고 재현 가능한 환경을 만들고 실행 결과에 lockfile·커밋·설정 해시를 연결한다. 런타임, 개발, 실제 API smoke 의존성을 그룹으로 분리한다. |
| 도메인 계약 | Pydantic v2, Python `dataclass`/`Enum` | 외부 입력 검증과 직렬화는 Pydantic, 계산 중심의 불변 값 객체는 dataclass로 분리한다. 필드 누락, 단위, 열거형, 명시적 `null` 사유를 계약으로 고정한다. |
| 비동기 I/O | `asyncio`, `httpx`, `websockets`, `tenacity` | REST/WebSocket 동시 수집, timeout, rate limit, 지수 백오프·jitter, 연결 격리를 지원한다. 공급자별 태스크와 회로 차단 상태를 분리한다. |
| 원시·정규화 저장 | 로컬 파일시스템의 불변 압축 원본 + Apache Parquet/PyArrow | 원 응답을 재현 가능하게 보존하고 대용량 시계열을 컬럼형·파티션형으로 저장한다. 원시 payload와 정규화 테이블은 물리 경로와 스키마를 분리한다. |
| 운영 메타데이터 | PostgreSQL 16 | 체크포인트, 공급자 상태, 계보, 이벤트 저널 인덱스, 신호·리스크·주문·체결 상태를 트랜잭션과 유일키로 보호한다. 여러 수집기의 동시 쓰기와 재시작 멱등성을 DuckDB보다 안정적으로 다룬다. |
| 연구 질의·변환 | DuckDB, Polars, PyArrow | Parquet 스냅샷을 복사 없이 분석하고 as-of join, 시계열 변환, 대용량 집계를 빠르게 수행한다. PostgreSQL을 대규모 연구 스캔 용도로 남용하지 않는다. |
| 통계 검증 | NumPy, SciPy, statsmodels, scikit-learn | bootstrap, permutation, 회귀·잔차화, Granger/lead-lag 및 시간순 검증에 필요한 검증된 구현을 제공한다. 난수 시드는 실험 메타데이터에 고정한다. |
| 이벤트 실행 | 프로젝트 내부의 타입드 이벤트 버스 + append-only 이벤트 저널 | 복수 거래소, 혼합 주기, 부분체결, 동일 timestamp 정렬, 가상 시계를 PRD에 맞게 통제한다. 초기 규모에 Kafka 같은 분산 인프라는 불필요하다. |
| CLI·스케줄링 | Typer CLI, APScheduler | 수집, 백필, 검증, 백테스트, 페이퍼 실행, 일일 리포트를 명시적 명령으로 제공한다. 작업은 스케줄러와 분리해 수동 재실행할 수 있고, macOS `launchd`는 프로세스 기동·감시만 담당한다. |
| 리포트·UI | Streamlit + Plotly 대시보드, JSON/Parquet 결과 내보내기 | PRD 15장의 운영 화면(세션·공급자 상태, 공정가·괴리, H1 압력, 신호·차단 사유, 페이퍼 포지션, 킬스위치)과 실험 리포트를 단일 로컬 웹앱으로 제공한다. 대시보드는 read-only로 PostgreSQL·Parquet 산출물만 조회하며 주문·설정 변경 기능을 갖지 않는다. 모든 차트의 원 데이터와 판정 사유를 JSON/Parquet로 함께 내보내 대시보드 없이도 결과가 보존·재현되게 한다. |
| 관측성 | 구조화 JSON 로그, Prometheus 형식 메트릭, OpenTelemetry trace ID | 공급자 연결, 지연, 큐 적체, 품질 차단, 이벤트 계보를 한 실행 ID로 추적한다. 초기에는 파일/로컬 endpoint로 내보내고 메트릭 전용 시각화(Grafana 등)는 선택 사항으로 남긴다. 운영 화면은 Streamlit 대시보드가 담당한다. |
| 비밀값 | 환경변수 + macOS Keychain 어댑터, `.env.example`에는 키 이름만 | 저장소·로그·fixture로의 유출을 막는다. 실제 smoke test만 사용자가 주입한 조회 전용 키를 사용한다. |
| 테스트·품질 | pytest, Hypothesis, respx, time-machine, Ruff, Pyright, `pytest-cov` | 계약·경계값·시간·재연결·결정론을 자동 검증한다. 실제 키 없는 테스트가 기본이며 실제 API 검증은 별도 마커로 격리한다. |
| 로컬 인프라 | Docker Compose의 PostgreSQL만 필수 서비스로 사용 | 개인 개발환경의 재현성을 확보하되 메시지 브로커, 분산 캐시, Kubernetes는 도입하지 않아 운영 복잡도를 제한한다. |

### 2.2 주요 선택의 이유와 보류한 대안

- **Backtrader/Zipline을 핵심 엔진으로 채택하지 않는다.** 단일 시장 bar 중심 가정에 맞추기 위해 PRD의 복수 세션, nanosecond 시각, 호가·틱 혼합, 두 다리 부분체결, 차입·리콜, 동일 timestamp 정렬을 우회 구현하면 오히려 검증이 어려워진다. 다만 통계 결과 교차검증용 보조 어댑터는 후속 실험으로 둘 수 있다.
- **Kafka/Redis를 Phase 0~3에 도입하지 않는다.** v1은 개인용 단일 호스트 연구 시스템이며, 타입드 인프로세스 버스와 PostgreSQL 이벤트 저널로 재생·감사·복구 요구를 충족할 수 있다. 큐 적체나 프로세스 분리가 실제 병목으로 측정될 때만 ADR을 작성해 재검토한다.
- **데이터 프레임은 Polars, 교환·저장은 Arrow/Parquet로 표준화한다.** pandas 객체나 공급자 SDK 응답을 영속 계약으로 삼지 않는다.
- **가격·금액은 전 구간에서 `float`를 금지하지는 않되 경계를 구분한다.** 주문·체결·비용·공정가의 권위 값은 `Decimal`/고정소수점으로 계산·저장하고, 통계 라이브러리 입력 복사본만 명시적 변환과 허용오차 기록 후 부동소수점을 사용한다.

## 3. 예정 디렉터리·모듈 구조

다음 구조는 Phase 0에서 생성한다. 디렉터리 이름은 책임 경계이며, 공급자·전략·실행 모듈 사이의 역참조를 금지한다.

```text
.
├── pyproject.toml
├── uv.lock
├── .env.example
├── configs/
│   ├── base.yaml
│   ├── environments/
│   ├── strategies/
│   ├── costs/
│   └── risk/
├── src/skhy_research/
│   ├── domain/
│   │   ├── market.py
│   │   ├── reference.py
│   │   ├── strategy.py
│   │   ├── execution.py
│   │   ├── risk.py
│   │   ├── experiment.py
│   │   └── enums.py
│   ├── ports/
│   │   ├── market_data.py
│   │   ├── reference_data.py
│   │   ├── historical_data.py
│   │   ├── broker.py
│   │   ├── storage.py
│   │   └── clock.py
│   ├── adapters/
│   │   ├── providers/
│   │   │   ├── krx/
│   │   │   ├── kis/
│   │   │   ├── toss/
│   │   │   ├── official_filings/
│   │   │   ├── naver/
│   │   │   └── yahoo/
│   │   ├── persistence/
│   │   ├── calendars/
│   │   ├── secrets/
│   │   └── reporting/
│   ├── data/
│   │   ├── catalog/
│   │   ├── ingestion/
│   │   ├── normalization/
│   │   ├── quality/
│   │   ├── reconciliation/
│   │   ├── lineage/
│   │   └── snapshots/
│   ├── features/
│   │   ├── common/
│   │   ├── h1_close_pressure/
│   │   ├── h2_adr_premium/
│   │   └── h3_price_discovery/
│   ├── strategies/
│   │   ├── registry.py
│   │   ├── h1_close_rebalance/
│   │   ├── h2_adr_convergence/
│   │   └── h3_nxt_nasdaq_leadlag/
│   ├── engine/
│   │   ├── events.py
│   │   ├── ordering.py
│   │   ├── bus.py
│   │   ├── replay.py
│   │   ├── backtest.py
│   │   ├── fill_models.py
│   │   ├── cost_models.py
│   │   └── portfolio.py
│   ├── paper/
│   │   ├── broker.py
│   │   ├── order_state.py
│   │   └── pair_coordinator.py
│   ├── risk/
│   │   ├── engine.py
│   │   ├── rules/
│   │   ├── sizing.py
│   │   └── kill_switch.py
│   ├── experiments/
│   │   ├── splits.py
│   │   ├── statistics.py
│   │   ├── promotion.py
│   │   └── registry.py
│   ├── reporting/           # 실험·일일 리포트 생성과 JSON/Parquet 내보내기
│   ├── dashboard/           # Streamlit 운영 대시보드 (PRD 15.1, read-only)
│   ├── observability/
│   ├── application/
│   └── cli.py
├── migrations/
├── tests/
│   ├── unit/
│   ├── property/
│   ├── contract/
│   ├── integration/
│   ├── replay/
│   ├── strategies/
│   ├── risk/
│   ├── e2e/
│   └── fixtures/sanitized/
├── docs/
│   ├── decisions/
│   ├── data_catalog/
│   ├── schemas/
│   └── runbooks/
├── var/                    # Git 제외: 로컬 실행 상태·리포트
└── data/                   # Git 제외: raw/normalized/features/signals/orders/fills
```

의존 방향은 `domain <- ports <- application <- adapters`로 유지한다. `strategies`는 도메인 계약과 승인된 feature reader만 의존하며 `adapters/providers`, `paper`, PostgreSQL에 직접 접근하지 않는다. `engine`과 `paper`는 공통 `BrokerProvider`, 주문 상태 머신, 비용·체결 모델을 사용한다. `dashboard`는 `reporting`·저장 계층 산출물의 read-only 소비자이며 수집·전략·주문 어떤 경로에도 역참조되지 않는다. 대시보드 프로세스 중단이 수집·페이퍼트레이딩에 영향을 주지 않아야 한다.

## 4. 아키텍처 설계

### 4.1 논리 흐름

```text
외부 공급자
  -> 공급자 포트/어댑터
  -> 원시 불변 기록 + 체크포인트
  -> 정규화/캘린더/품질·공급자 대조
  -> 시점 고정 특징(as-of)
  -> 전략 플러그인
  -> Signal
  -> 리스크 엔진(ALLOW/BLOCK/REDUCE)
  -> OrderIntent
  -> 이벤트 기반 백테스트 또는 PaperBrokerProvider
  -> PaperFill/포지션/손익
  -> 실험·일일 리포트와 PASS/HOLD/REJECT
```

각 화살표는 새 레코드를 생성한다. 앞 계층을 덮어쓰거나 나중 계층의 정보를 앞 계층에 역주입하지 않는다. 실시간 처리와 과거 재생은 동일한 흐름을 사용하며 데이터 source와 `Clock` 구현만 다르다.

### 4.2 데이터 계층

| 계층 | 권위 데이터와 저장 방식 | 핵심 키·불변조건 | 생성 책임 | 관련 FR |
| --- | --- | --- | --- | --- |
| 원시(raw) | HTTP 응답 본문, WebSocket frame, 공식 문서 메타데이터를 수신 순서대로 압축 불변 저장 | `raw_record_id`, source, endpoint/stream, 수신시각, payload hash, 수집 실행 ID, cursor; append-only | 공급자 어댑터와 raw recorder | FR-02, FR-03, FR-16 |
| 정규화(normalized) | `RecordEnvelope`, `MarketQuote`, `Trade`, `Bar`, `FundSnapshot`, `FXQuote`, 기준정보 | 안정 `instrument_id`, UTC ns, 원시 timezone/timestamp, 통화, 세션, 조정상태, 품질 플래그, `raw_record_id`; 수정은 새 버전 | normalizer, calendar resolver, reconciler | FR-04, FR-05, FR-06, FR-16 |
| 특징(features) | H1 압력, H2 표시·실행 가능 프리미엄, H3 동기화·잔여수익 등 시점 고정 입력 | `feature_set_id`, `as_of_time`, 입력 ID 목록/manifest, 알고리즘 버전, 설정 해시; `available_time_utc <= as_of_time` | feature builder | FR-07, FR-09, FR-10, FR-11, FR-16 |
| 신호(signals) | 전략별 방향, 신뢰도, 예상 총수익·비용·순수익, 만료 | strategy ID/version, feature set, 데이터 snapshot, 생성 시각; 전략 간 독립 저장 | strategy plugin | FR-09, FR-10, FR-11, FR-16 |
| 주문(orders) | 페이퍼 전용 `OrderIntent`, 다리·수량·지정가·TIF·헤지비율, `RiskDecision` | signal ID, 리스크 전후 수량, 사유코드, idempotency key, 주문 상태 전이; `BLOCK`에는 외부 주문 없음 | risk engine, order planner, paper broker | FR-08, FR-12, FR-13, FR-14, FR-16 |
| 체결(fills) | `PaperFill`, 미체결, 부분체결, 취소·만료, 사용 호가·거래량, 비용·슬리피지 | order ID, fill model/version, market event IDs, 가상/실시간 시계, 포지션·현금 원장 연결; append-only | backtest execution, paper broker | FR-12, FR-13, FR-15, FR-16 |

원시 데이터는 `source/dataset/event_date/hour` 파티션을 기본으로 하되 공급자 라이선스에 따라 보관 경로와 기간을 설정한다. 정규화 이후 계층은 schema version을 파티션·manifest에 포함한다. PostgreSQL에는 catalog, 체크포인트, 유일키, lineage edge, 실행·주문 상태를 저장하고, 대량 사실 데이터는 Parquet에 저장한다. 저장 경계는 레코드 성격으로 고정한다: 틱·호가·trade·bar 등 시계열 사실 레코드 본문은 유량과 무관하게 항상 Parquet에 두고 PostgreSQL에 행 단위로 적재하지 않는다. PostgreSQL 이벤트 저널에는 주문·체결·리스크·킬스위치·시스템 상태 전이와 Parquet segment 인덱스(경로, 구간, 레코드 수, checksum)만 기록해 시세 유량 급증이 트랜잭션 저장소의 I/O를 잠식하지 않게 한다. DuckDB snapshot은 Parquet manifest의 고정 목록만 읽어 실행 중 데이터 유입으로 결과가 변하지 않게 한다.

중복 제거 키는 공급자 sequence가 있으면 이를 우선하고, 없으면 `source + instrument_id + event type + event_time + payload hash`를 사용한다. 충돌 레코드는 조용히 버리지 않고 중복·불일치 상태와 원본 ID를 보존한다.

### 4.3 공급자 포트와 어댑터

모든 공급자 구현은 공통으로 `capabilities`, 호출·구독 한도, 예상·측정 지연, 저장·재배포 조건, 이용약관 URL, 마지막 확인시각, 건강상태를 노출한다. 미지원 기능은 빈 배열이 아닌 `UNSUPPORTED_CAPABILITY`로 반환한다.

| 포트 | 입력·출력 계약 | 계획된 구현 | 장애·대체 정책 | 관련 FR |
| --- | --- | --- | --- | --- |
| `MarketDataProvider` | quote/trade/예상체결의 구독·해제, 연결상태, 지연, 재연결, 누락구간 정보 | KIS 기본, Toss 교차검증 | 공급자별 격리·백오프 후 공식 REST로 가능한 누락만 보충. Naver/Yahoo로 실시간 자동대체 금지 | FR-02, FR-03, FR-05 |
| `ReferenceDataProvider` | 종목 마스터, venue/session, 기업행사, 상품 구조, ADR 비율, 전환 상태 | KRX, SEC/KIND/HKEX/발행사, Citi/KSD 자료 어댑터 | 공식 출처 우선순위와 효력·공개시각을 저장. 충돌은 `SOURCE_DIVERGENCE`와 차단 | FR-02, FR-04, FR-06, FR-08 |
| `HistoricalDataProvider` | 기간·주기·조정방식이 명시된 bar/통계와 lineage | KRX 권위 원장, KIS/Toss 보조, Naver/Yahoo 백필·대조 | 공급자를 조용히 이어 붙이지 않고 구간별 source segment를 기록 | FR-02, FR-03, FR-04, FR-05 |
| `BrokerProvider` | 계좌 snapshot, 주문·취소 요청, 주문·체결·잔고·손익 이벤트 | `PaperBrokerProvider`만 구현·등록 | KIS/Toss 실주문 구현은 v1에 존재하지 않음. 알 수 없는 broker mode면 시작 실패 | FR-13, FR-14 |

capability probe는 계정·환경별 결과를 versioned catalog에 기록한다. 문서 명세와 실제 조회 결과가 다르면 더 좁은 capability를 채택하고 G-02 결정 기록을 갱신한다.

### 4.4 전략 플러그인

각 플러그인은 manifest와 순수 계산부로 구성한다. manifest에는 `strategy_id`, semantic version, 입력 feature schema, 의사결정 스케줄, 허용 venue/session, 필수 capability, warm-up 구간, 비용 모델, 주문 플래너, 최대 신호 수명, 설정 schema를 둔다.

공통 실행 계약은 다음 책임으로 제한한다.

- `initialize(context)`: 고정 설정, 데이터 snapshot, 가상/실시간 clock, read-only feature reader를 받는다.
- `on_event` 또는 `on_snapshot`: 허용된 시각까지의 특징만 읽고 `Signal` 또는 명시적 no-signal 사유를 반환한다.
- `explain`: 입력 ID, 중간 계산, 임계값, 차단 전 신호 사유를 구조화해 감사 추적에 제공한다.
- 공급자 호출, 데이터 저장, 주문 제출, 전역 포지션 변경은 전략 내부에서 금지한다.

전략별 구현 경계는 다음과 같다.

- **H1 종가 리밸런싱**: 15:10 KST snapshot, 15:19:30 주문 의도 마감, 전일까지 공개된 AUM/NAV와 당시까지 공개된 flow만 as-of join한다. 지정가 진입은 PRD 9.1의 15:10~15:20 구간에서만 시뮬레이션하고, 사후 검증에서는 실제 종가 경매 거래대금·불균형 대용치, 15:20 이후 잔여수익, `000660` ADV 대비 압력을 이론 흐름과 각각 비교한다. `kappa`는 학습 구간·상품·regime별 버전으로 관리한다. 예상체결/프로그램 피드가 없으면 결측 상태를 보존한 축소모델을 별도 버전으로 실행한다. [FR-09]
- **H2 ADR 수렴**: 1 보통주 대 10 ADR, 동시시장, 신선 호가, FX bid/ask, `ConversionStatus=OPERATIONAL`, 유효 `BorrowQuote`/승인 대체헤지, 모든 비용 후 양의 순괴리를 필수 전제한다. 기본 방향은 보통주 롱·ADR 숏이며 단독 본주 롱을 H2로 분류하지 않는다. [FR-07, FR-08, FR-10]
- **H3 동시 가격발견**: calendar/DST로 계산한 실제 교집합에서 1초·5초·1분 표본을 분리한다. 공통 반도체·환율 요인 통제 전후 통계와 최초 실행 가능 호가 이후 모의손익을 함께 낸다. 최대 30분 또는 동시거래 종료까지를 기본 보유로 한다. [FR-06, FR-11]

### 4.5 이벤트 기반 백테스트 엔진

엔진은 event sourcing 방식의 단일 결정론적 루프를 사용한다.

1. 과거 데이터 snapshot manifest를 고정하고 `SimulationClock`을 시작한다.
2. 외부 이벤트는 시스템이 알 수 있게 된 `available_time_utc`를 1차 키로 정렬한다. 실시간 레코드는 원칙적으로 `received_time_utc`, 공식 기준정보는 `published_at`, bar는 해당 bar 종료와 실제 공개시각 중 늦은 시각을 사용한다. 그 안에서 `event_time_utc`, 공급자 sequence, venue 우선순위, event type rank, 안정 event ID 순으로 정렬하며 이 규칙 자체에 버전을 부여한다. 공개시각을 신뢰할 수 없으면 실행 가능 데이터로 쓰지 않고 품질 플래그를 남긴다.
3. 같은 외부 이벤트에서 파생된 처리는 `시장·기준 이벤트 -> 특징 갱신 -> 전략 신호 -> 리스크 판정 -> 주문 의도 -> 체결 -> 포트폴리오·손익` 순으로 수행한다.
4. bar는 종료시각에만 공개된 것으로 처리하고, 주문은 생성 시각 이후에 수신된 호가·거래량에서만 체결 가능하다.
5. 모든 이벤트와 상태 전이를 append-only journal에 기록하고 checkpoint에서 중단·재개한다.

엔진이 모델링할 항목은 다음과 같다.

- 복수 venue/session/currency와 KRX/NXT/Nasdaq/HKEX calendar, DST, 휴장 불일치
- 틱, quote, 1초·5초·1분·일봉 및 reference/timer 이벤트 혼합
- 지정가 도달, 호가 깊이·참여율 기반 부분체결, 미체결, 만료, 취소
- 두 다리 주문의 leg별 상태, leg timeout, 잔여 노출과 강제 헤지/청산 시뮬레이션
- 거래정지, VI, 가격제한폭, 세션 종료, stale quote
- 수수료, 세금, bid-ask, 슬리피지·시장충격, FX, ADR 비용, 차입금리·리콜, 상품 추적오차·보수
- 통화별 현금 원장과 평가환율, 실현·미실현 손익, exposure time

체결모델은 `fill_model_version`과 주문 크기·호가 참여율 등의 설정을 결과에 남긴다. 완전히 같은 snapshot, commit, config, seed, ordering version으로 두 번 실행한 결과의 이벤트 해시와 지표가 일치해야 한다. [FR-01, FR-12, FR-16]

### 4.6 페이퍼 브로커

`PaperBrokerProvider`는 실제 주문 없이 백테스트와 동일한 주문 상태 머신·체결·비용 모델을 실시간 clock에 연결한다.

- 상태 전이는 `CREATED -> RISK_ACCEPTED -> OPEN/PARTIALLY_FILLED -> FILLED/CANCELLED/EXPIRED/REJECTED`로 제한하고 모든 전이를 이벤트로 남긴다.
- order idempotency key로 재시작·재전송 시 중복 주문을 막는다.
- quote/trade가 지연 또는 역순이면 체결을 만들지 않고 품질·리스크 이벤트를 발생시킨다.
- pair coordinator는 두 다리를 동시에 제출한 것처럼 관리하되 실제 leg별 체결을 숨기지 않는다. timeout을 넘으면 신규 pair를 차단하고 사전 정의된 헤지/청산 시뮬레이션을 실행한다.
- 잔고, 현금, 평균단가, 실현·미실현 손익, 비용, borrow accrual을 event journal에서 재구성할 수 있어야 한다.
- broker registry에서 `paper` 이외 mode가 설정되면 애플리케이션 부팅을 실패시킨다. [FR-13, FR-16]

### 4.7 리스크 엔진

리스크 엔진은 모든 `OrderIntent` 앞에서 동기적으로 실행하고 `ALLOW`, `BLOCK`, `REDUCE`와 사유코드, 적용 전후 수량, 사용 한도 snapshot을 기록한다.

검사 순서는 다음과 같다.

1. **구성·계보**: 승인 strategy/schema/config version, commit, snapshot 확인
2. **데이터 건강**: 핵심 공급자 연결, freshness(기본 H1 2초, H2/H3 5초), source divergence, 로컬 시계 오차
3. **시장 상태**: calendar/session, 휴장, 정지, VI, 가격제한폭, 호가 유효성
4. **전략별 거래 가능성**: H2 전환·차입·대체헤지, H1 필수 피드, H3 동시시장
5. **경제성**: 모든 예상비용이 총 기대수익보다 작은지, 기본·스트레스 비용 상태
6. **수량**: 계좌 기준자산 0.25% 거래당 위험, stop distance, 최소 거래단위, 호가 유동성 중 가장 보수적인 수량으로 `REDUCE`
7. **포트폴리오·킬스위치**: 일손실 1%, 누적 MDD 5%, 한쪽 다리 timeout, 차입 리콜, 전략·자산·통화 노출

킬스위치는 system/account/strategy 계층으로 나누고 발동 원인, 시각, 해제 조건, 수동 승인 여부를 저장한다. 자동 해제 가능한 일시적 데이터 장애와 사용자 확인이 필요한 일손실·MDD를 구분한다. 위험 판단에 필요한 값이 없으면 `ALLOW`가 아닌 `BLOCK`을 반환한다. [FR-08, FR-14, FR-16]

### 4.8 재현성·감사 추적

모든 실행에 `run_id`를 부여하고 다음 묶음을 immutable manifest로 만든다.

- 저장소 commit, Python·lockfile 버전, strategy/feature/fill/cost/ordering/schema version
- 정규화 데이터 snapshot ID와 실제 Parquet 파일·checksum 목록
- 비밀값을 제거한 canonical config와 hash
- 시간순 split, seed, 시작·종료 시각, 공급자 catalog version
- signal -> feature -> normalized -> raw의 lineage edge
- signal -> risk decision -> order -> fill -> position/PnL의 execution edge

리포트의 수치 하나에서 사용 레코드와 계산 버전까지 양방향 탐색할 수 있게 하며, 민감정보는 manifest와 로그에 포함하지 않는다. [FR-01, FR-16]

## 5. Phase 0~3 단계별 작업 분해

### 5.1 전체 의존 순서

`Phase 0 기반·계약·접근 확인 -> Phase 1 공식 일별/H1/엔진 최소판 -> Phase 2 실시간 교차시장/H2·H3/페어 체결 -> Phase 3 전진 페이퍼·최종 판정` 순서를 고정한다. 다음 Phase의 개발 브랜치는 준비할 수 있어도, 이전 Phase 완료조건을 통과하지 않은 데이터로 전략 결과를 승격하지 않는다.

### 5.2 Phase 0 — 기반과 데이터 접근

| 작업 ID | 작업·산출물 | 선행조건 | 완료 검증 | FR 역참조 |
| --- | --- | --- | --- | --- |
| P0-01 | Python/uv 프로젝트, 품질도구, 로컬 PostgreSQL, Git 제외 규칙, 환경별 config loader 구성 | 없음 | 실제 키 없이 설치·lint·typecheck·test 명령 재현 | FR-01 |
| P0-02 | commit/lockfile/config hash/data snapshot/run ID를 만드는 실행 manifest와 lineage 기본 schema 정의 | P0-01 | 동일 설정 hash 일치, 비밀값 제외, manifest schema test | FR-01, FR-16 |
| P0-03 | Keychain/환경변수 secret 주입, 조회 전용 profile, 로그·예외 마스킹, `paper` broker만 허용하는 부팅 gate 정의 | P0-01 | 가짜 secret 누출 탐지 test, 실주문 mode 부팅 실패 | FR-02, FR-13, FR-16 |
| P0-04 | `RecordEnvelope`와 핵심 도메인 타입, Decimal·통화·UTC ns·null 사유·quality flag schema 정의 | P0-01 | 직렬화 round-trip, 단위·반올림·열거형 property test | FR-04, FR-05, FR-16 |
| P0-05 | 내부 instrument master와 원천 symbol alias, 기업행사 version, KRX/NXT/Nasdaq/HKEX calendar·DST·session resolver 구축 | P0-04 | 휴장·DST·세션·심볼 변경 fixture test | FR-04, FR-06, FR-16 |
| P0-06 | 네 개 공급자 포트와 provider registry, capability/license/latency catalog schema 구현 계획 확정 | P0-04 | 미지원 capability가 명시 오류로 반환, 역할·이용조건 조회 | FR-02 |
| P0-07 | KRX/KIS/Toss/공식 공시/Naver/Yahoo의 sanitized fixture 어댑터와 capability probe, 오류 매핑 작성 | P0-03, P0-06, G-02 | 인증 실패·만료·rate limit·schema drift 계약 test | FR-02, FR-03, FR-05 |
| P0-08 | raw recorder, checksum, source cursor/checkpoint, dedupe key, 재시작 catch-up, 불변 partition/manifest 구현 설계 확정 | P0-02, P0-06, G-06 | 강제 중단 후 중복 없는 재개와 원본 checksum 불변 test | FR-03, FR-16 |
| P0-09 | 정규화 pipeline, 공급자별 anti-corruption mapping, 품질 탐지, source reconciliation과 신호 차단 상태 구축 | P0-04, P0-05, P0-08 | 중복·역순·gap·음수·bid>ask·source divergence fixture test | FR-04, FR-05, FR-06, FR-16 |
| P0-10 | 공급자 건강상태·수신지연·clock drift·누락 보충 상태의 구조화 로그/메트릭·상태 리포트 작성 | P0-07~P0-09 | 공급자 하나의 장애가 다른 수집 태스크와 저장을 중단시키지 않음 | FR-02, FR-03, FR-05 |
| P0-11 | G-01~G-08 gate register와 증거·확인시각·유효기간·결론을 담는 결정 기록 템플릿 운영 시작 | P0-01 | 미확인/만료 gate가 관련 기능에서 `BLOCK`됨 | FR-02, FR-08, FR-14, FR-16 |
| P0-12 | 전체 fixture 계약 test와 사용자 키 주입 환경의 조회 전용 smoke test runbook 실행 | P0-01~P0-11 | 실제 키 없는 CI 통과, 별도 smoke 결과·capability catalog 저장, 주문 endpoint 호출 0건 | FR-01, FR-02, FR-03, FR-04, FR-05, FR-06, FR-13, FR-16 |

Phase 0 완료조건:

- fixture 기반 공급자 계약·정규화·캘린더·재시작 테스트가 통과한다.
- 사용자가 조회 전용 키를 주입한 환경에서 KRX/KIS/Toss capability smoke가 통과하거나, 실패 capability가 명시적으로 비지원·차단 상태로 기록된다.
- 원시 레코드 하나에서 source, 수신시각, checksum, 이용조건, 정규화 레코드까지 추적된다.
- broker registry와 배포 산출물에 실주문 구현이 없다.

### 5.3 Phase 1 — 공식 일별 데이터와 H1 연구

| 작업 ID | 작업·산출물 | 선행조건 | 완료 검증 | FR 역참조 |
| --- | --- | --- | --- | --- |
| P1-01 | KRX 공식 일별 OHLCV·상품·파생·종목 기준정보 백필과 KIS/보조 공급자 대조 | Phase 0, G-04, G-06 | 최소 120 KRX 거래일 snapshot, 구간별 source·조정상태 확인 | FR-02, FR-03, FR-04, FR-05, FR-06, FR-16 |
| P1-02 | 국내 단일종목 상품과 HKEX 7709의 AUM/NAV/iNAV/PCF/발행좌수/배율/복제방식 수집 및 `published_at`·`effective_at` 분리 | P1-01, G-04 | 상품 동적 발견, 공개시각 누락 상품 제외·사유 기록 | FR-03, FR-04, FR-05, FR-09, FR-16 |
| P1-03 | H1 theoretical exposure, flow adjustment, ADV 정규화 close pressure feature set 구현 계획 구체화 | P1-02, G-03 | beta 2/-1/-2 부호, 상품·regime별 kappa, 결측 feed 축소모델 검증 | FR-09, FR-16 |
| P1-04 | 15:10 snapshot/15:19:30 cutoff를 강제하는 H1 플러그인과 양방향 신호·반증 사유 구현 | P1-03 | 당일 장후 확정 AUM/NAV 주입 시 test 실패 | FR-06, FR-09, FR-16 |
| P1-05 | 결정론적 이벤트 루프, SimulationClock, event ordering, limit/partial fill, halt/VI/가격제한, portfolio ledger의 최소판 구축 | P0-04~P0-09 | 동일 run 2회의 event/result hash 일치, 미래 호가 체결 0건 | FR-12, FR-13, FR-16 |
| P1-06 | H1 지정가 진입·종가경매/NXT 종료 체결모델과 수수료·세금·spread·slippage·impact 비용 모델 구축 | P1-05, G-08 | 부분·미체결, 경매 feed 부재, 기본/2배 비용 시나리오 | FR-12, FR-13, FR-14 |
| P1-07 | 시간순 60/30/30 split과 확장 walk-forward, 학습 구간 전용 kappa/threshold tuning, snapshot·seed registry 구축 | P1-01~P1-06 | test 구간을 읽은 tuning 차단, 버전 변경 시 새 test 요구 | FR-01, FR-09, FR-12, FR-16 |
| P1-08 | 기대값, 중앙 거래손익, PF, MDD, ES, turnover, exposure, slippage, 미체결, 집중도, bootstrap/permutation 리포트 생성 | P1-07 | 기본·2배 비용, 최고 1/3일 집중도, CI와 PASS/HOLD/REJECT 출력 | FR-15, FR-16 |
| P1-09 | H1 데이터·전략·체결·리스크 테스트 및 룩어헤드 lineage 감사 수행 | P1-01~P1-08 | PRD 14.2~14.4의 H1 항목과 Phase 1 품질 gate 통과 | FR-04, FR-05, FR-09, FR-12, FR-14, FR-15, FR-16 |

Phase 1 완료조건:

- 신뢰 가능한 H1 데이터가 최소 120 KRX 거래일이며 60/30/30 시간순 분할과 이후 walk-forward 결과가 재현된다.
- 15:10 시점 신호에 사후 공개 AUM/NAV가 포함되지 않았다는 lineage 감사가 통과한다.
- 기본 비용과 각 비용 2배 결과, 집중도, 신뢰구간, 반증 지표를 포함한 리포트로 H1을 `PASS`, `HOLD`, `REJECT` 중 하나로 판정한다.
- G-03 미확정이면 완전모델을 가장하지 않고 축소모델 버전과 품질 경고를 명시한다.

### 5.4 Phase 2 — 교차시장 기록과 H2/H3 연구

| 작업 ID | 작업·산출물 | 선행조건 | 완료 검증 | FR 역참조 |
| --- | --- | --- | --- | --- |
| P2-01 | KIS 기본·Toss 대조로 KRX/NXT/Nasdaq quote/trade와 USD/KRW bid/ask 실시간 기록, 재연결·gap backfill | Phase 0, G-02, G-06 | 공급자별 지연·세션·source segment, 중단 복구, stale 차단 | FR-02, FR-03, FR-04, FR-05, FR-06, FR-16 |
| P2-02 | `ConversionStatus`, ADR ratio, `BorrowQuote`, 대체헤지 capability의 효력·확인·만료 registry | G-01, G-05, G-07 | UNKNOWN/SUSPENDED/만료/차입불가에서 주문 의도 0건 | FR-02, FR-08, FR-14, FR-16 |
| P2-03 | ADR 표시용 fair value와 bid/ask 기반 진입·청산 실행 가능 premium feature, stale reference 분리 | P2-01 | USD/KRW 방향, 10:1, bid/ask 다리, 동시각 허용범위 property test | FR-04, FR-05, FR-07, FR-16 |
| P2-04 | H2 플러그인: 보통주 롱·ADR 숏 기본 방향, 최소단위, FX·전환·차입·ADR·세금·slippage 포함 순괴리와 종료조건 | P2-02, P2-03 | 비용 하나라도 빠지면 실험 실패, 단독 본주 롱을 H2로 생성하지 않음 | FR-07, FR-08, FR-10, FR-14, FR-16 |
| P2-05 | exchange calendar 교집합과 1초/5초/1분 동기화 dataset, 공통요인 원시·잔여수익 feature 구축 | P2-01 | DST 전환·한미 휴장 불일치에서 고정 KST 구간 사용 0건 | FR-04, FR-05, FR-06, FR-11, FR-16 |
| P2-06 | H3 플러그인: 양방향 lead-lag/Granger/교차상관/event response와 최초 실행 가능 호가 기반 모의손익 | P2-05 | 통제 전후 결과, 비정상 체결 영향, 30분/동시종료 보유 제한 보고 | FR-11, FR-12, FR-15, FR-16 |
| P2-07 | 백테스트 엔진을 복수 venue/currency, pair coordinator, leg timeout, borrow accrual/recall, FX ledger로 확장 | P1-05, P2-01~P2-04, G-08 | 부분체결·한쪽 다리·리콜·시장정지·가격제한·유동성 절반 stress | FR-12, FR-13, FR-14, FR-16 |
| P2-08 | H2/H3 시간순·walk-forward 실험, 짧은 실제 역사 표시, 유사종목 방법 검증 결과의 명확한 분리 | P2-04~P2-07 | 2026-07-10 이전 SKHY 실제 데이터 사용 0건, CAGR 과장 없음 | FR-01, FR-10, FR-11, FR-12, FR-15, FR-16 |
| P2-09 | 교차시장 상태·공정가·premium·두 다리·비용·차단 사유 연구 리포트 | P2-08 | 기본/2배 비용, 집중도, CI, 반증·표본부족 상태 포함 | FR-07, FR-10, FR-11, FR-15, FR-16 |
| P2-10 | DST·휴장·stale·source divergence·전환·차입·pair failure의 replay/e2e test suite 완성 | P2-01~P2-09 | PRD Phase 2 완료조건과 14장 관련 항목 모두 통과 | FR-05, FR-06, FR-07, FR-08, FR-09, FR-10, FR-11, FR-12, FR-13, FR-14, FR-15, FR-16 |

Phase 2 완료조건:

- DST, 거래소 휴장 불일치, 한쪽 다리 부분/미체결, 오래된 시세 비교가 모두 안전하게 재생되고 예상된 `BLOCK` 또는 손익을 만든다.
- H2는 `ConversionStatus=OPERATIONAL`과 유효 borrow/대체헤지가 모두 입증된 구간에서만 거래 가능 결과로 표시한다. 미입증 시 연구용 표시 premium만 계산하고 신규 주문은 생성하지 않는다.
- H3는 동시거래 구간과 공통요인 통제 전후 결과, 최초 실행 가능 호가 이후 손익을 분리해 보고한다.
- H2/H3의 짧은 역사를 SK하이닉스의 장기 성능으로 합성하거나 CAGR로 과장하지 않는다.

### 5.5 Phase 3 — 전진 페이퍼트레이딩

| 작업 ID | 작업·산출물 | 선행조건 | 완료 검증 | FR 역참조 |
| --- | --- | --- | --- | --- |
| P3-01 | 실시간 application orchestrator에 세 전략 registry, live clock, feature stream, risk engine, paper broker 연결 | Phase 1~2 | 실주문 endpoint·adapter 0개, 전략별 독립 enable/disable | FR-09~FR-14 |
| P3-02 | 주문 상태 머신·pair coordinator·현금/포지션/PnL·재시작 복구를 운영 수준으로 완성 | P3-01 | 강제 재시작 후 중복 주문·체결 없이 journal에서 상태 복구 | FR-03, FR-12, FR-13, FR-16 |
| P3-03 | 데이터·시장·전환·차입·비용·수량·일손실·MDD·leg timeout 킬스위치와 해제 절차 운영 | P3-01 | 모든 주문 의도에 decision 존재, 결측 시 fail-closed | FR-05, FR-08, FR-14, FR-16 |
| P3-04 | 데이터 완전성, 신호/주문/체결, 예상 대비 실제, 비용 분해, 표본·판정 진행상태, 공식 상태 변경의 일일 리포트와 운영 상태 데이터 계약 확정 | P3-01~P3-03 | 장 종료 후 idempotent 생성, 누락 시 명시적 경고 | FR-01, FR-05, FR-15, FR-16 |
| P3-05 | 공급자·세션·지연, H1 압력, ADR fair/premium, 전략 신호·비용·차단, 포지션·두 다리·PnL·킬스위치의 Streamlit 운영 대시보드 구축 (P3-04의 리포트·데이터 계약을 read-only로 소비) | P3-01~P3-04 | PRD 15.1 항목을 동일 run ID와 시점으로 조회, 대시보드 중단이 수집·페이퍼 실행·리포트 생성에 무영향 | FR-02, FR-05, FR-07, FR-13~FR-16 |
| P3-06 | 60거래일 전진 관측과 전략별 최소 30개 적격 신호 수집; 장애·휴장·고변동일은 사전 규칙으로만 제외 | P3-01~P3-05 | 날짜·신호 수, 제외 사유, 데이터 품질의 감사 추적 | FR-03, FR-05, FR-09~FR-16 |
| P3-07 | 비용·유동성 stress, 집중도, bootstrap/permutation, 승격 기준 자동 평가 | P3-06 | 기대값>0, PF>=1.2, 2배 비용 PnL>=0, 최고 하루<=30%, MDD<=5%, 위험한도 판정 | FR-12, FR-14, FR-15, FR-16 |
| P3-08 | 전략별 최종 PASS/HOLD/REJECT와 반증·표본부족·운영 위험을 담은 종료 보고서 | P3-07 | 판정 근거가 고정 snapshot과 원시 데이터까지 역추적됨 | FR-01, FR-15, FR-16 |

Phase 3 완료조건:

- H2/H3는 최소 60거래일, 모든 전략은 전략별 적격 신호 최소 30건 요건을 충족하거나 표본 부족으로 명시적으로 `HOLD`다.
- 모든 signal에 risk decision이 있고, 모든 fill은 시장 이벤트·체결모델·비용·주문·signal로 역추적된다.
- PRD 승격 기준을 변경하지 않은 자동 판정으로 각 전략을 최종 분류한다.
- 실주문 경로가 없고 비밀값이 Git 상태·로그·fixture·리포트에 나타나지 않는다.

## 6. 테스트 전략

### 6.1 테스트 피라미드와 실행 환경

| 계층 | 목적 | 실행 시점 |
| --- | --- | --- |
| 정적 검증 | Ruff, Pyright, 의존방향, config/schema 유효성, 금지된 broker adapter 탐지 | 모든 변경 |
| 단위·property | Decimal·단위·timestamp·calendar·수식·상태 전이·경계값 불변조건 | 모든 변경 |
| 공급자 계약 | fixture 응답, 오류 schema, capability, schema drift, 마스킹 | provider 변경 및 일일 CI |
| 데이터 품질·대조 | 중복·역순·gap·가격/호가 이상·세션·수정주가·source divergence | ingestion/normalization 변경 |
| 전략·룩어헤드 | H1/H2/H3 방향, 입력시점, 필수비용, gate, 반증조건 | feature/strategy 변경 |
| replay·결정론 | 고정 이벤트 journal을 두 번 재생해 event/result hash 비교 | engine/fill/risk 변경 |
| 통합·E2E | raw fixture에서 report까지, 장애·재시작·부분체결·킬스위치 | Phase gate와 release candidate |
| 대시보드 | read-only 강제, 비밀값 미노출, stale·빈 데이터 상태, reporting 데이터 계약 일치, 장애 격리 | dashboard/reporting 변경 |
| 실제 API smoke | 사용자가 주입한 조회 전용 key로 최소 조회·구독·재연결 | 수동/예약 환경, 주문 호출 금지 |
| 통계 검증 | 시간순 split, walk-forward, bootstrap/permutation, stress, 판정 | 실험 snapshot 고정 후 |

테스트 fixture는 실제 키·계좌·개인정보를 제거한 raw payload와 기록 재생 파일을 사용한다. 날짜·시간은 injectable clock으로 고정하고 네트워크는 계약 테스트에서 차단한다. 실제 API smoke에는 별도 marker와 환경 gate를 적용하며 결과 payload에도 비밀값을 저장하지 않는다.

### 6.2 PRD 14장 반영 체크리스트

#### 공급자 계약 테스트

- KRX/KIS/Toss 인증 성공·실패, token 만료, rate limit, timeout, 공급자 오류 schema를 fixture와 mock server로 재현한다. [FR-02, FR-03]
- REST/WebSocket 중단, 지수 백오프·jitter, 재연결, cursor 이후 누락 보충, 중복 방지를 fault injection으로 검증한다. [FR-03, FR-05]
- symbol/venue/currency/session mapping과 `UNSUPPORTED_CAPABILITY`를 계약 test로 고정한다. [FR-02, FR-04, FR-06]
- 저장된 schema fingerprint와 새 payload를 비교해 필드 추가·삭제·타입 변경을 감지하고 격리 queue로 보낸다. [FR-04, FR-05]
- 로그·예외·report·fixture에 심은 canary secret이 출력되지 않는지 검사한다. [FR-01, FR-16]

#### 데이터 품질 테스트

- 중복, 역순, 시간 공백, 음수·0 가격, `bid > ask`, stale/delayed를 단위와 property test로 탐지한다. [FR-05]
- KRX 공식 일별 OHLCV와 broker 집계를 tolerance와 세션 기준에 따라 대조하고 초과 시 `SOURCE_DIVERGENCE`로 차단한다. [FR-05]
- KRX와 NXT의 개별·통합 세션, 거래량, 종가를 섞지 않는 fixture를 둔다. [FR-04, FR-06]
- 기업행사 전후 raw/adjusted series와 조정 버전을 분리하며 원시 데이터를 덮어쓰지 않는다. [FR-03, FR-04]
- USD/KRW 방향, ADR 10:1, 통화·반올림을 예제 기반과 property test로 검증한다. [FR-07]
- 오래된 한국 종가와 미국 실시간가 조합은 표시 계산만 허용하고 `stale_reference`로 강제하며 주문 의도는 막는다. [FR-05, FR-07, FR-14]

#### 전략 테스트

- H1 `as_of_time` 뒤에 공개된 AUM/NAV가 query 결과나 lineage에 들어오면 실패시키는 미래 데이터 canary를 둔다. [FR-09, FR-16]
- `beta=2`, `-1`, `-2` 이론 노출 부호와 `kappa` 학습구간 제한을 고정한다. [FR-09]
- H2 양의 ADR premium의 기본 다리가 보통주 long·ADR short이고 1:10 경제비율인지 검증한다. [FR-07, FR-10]
- `ConversionStatus=UNKNOWN/ANNOUNCED/SUSPENDED`, 만료 conversion, borrow 없음/만료에서 신규 H2 order가 0건인지 검증한다. [FR-08, FR-10, FR-14]
- H3 DST 전환일, 한미 휴장 불일치, 동시시장 경계, 저유동성 이상체결을 고정 fixture로 재생한다. [FR-06, FR-11]
- 수수료, 세금, FX, spread, borrow, ADR 비용, slippage 중 전략별 필수항목 하나를 제거한 mutation이 실험 실패를 만드는지 확인한다. [FR-10, FR-12, FR-15]

#### 대시보드 테스트

- 대시보드 코드에 주문·설정 변경·수집 제어 경로가 없음을 정적 검증(의존방향 검사)과 계약 test로 고정한다. [FR-13, FR-14]
- 대시보드 렌더링 결과와 로그에 canary secret이 노출되지 않는지 검사한다. [FR-16]
- stale·빈 데이터, 리포트 미생성, PostgreSQL/Parquet 접근 불가 상태에서 대시보드가 오류 은폐 없이 품질 플래그와 함께 표시되는지 검증한다. [FR-05, FR-15]
- 대시보드가 소비하는 데이터 계약이 reporting 산출물 schema와 일치하는지 fixture로 검증하고, schema 변경 시 계약 test가 실패해야 한다. [FR-15, FR-16]
- 대시보드 프로세스 강제 종료가 수집·페이퍼 실행·일일 리포트 생성에 영향을 주지 않는지 통합 test로 확인한다. [FR-03, FR-13]

#### 체결·리스크 테스트

- partial/unfilled/one-leg-only/limit-not-reached/order-expiry와 leg timeout 후 헤지·청산을 scenario test로 검증한다. [FR-12, FR-13, FR-14]
- 거래정지, VI, 가격제한폭, 급격한 spread 확대, stale quote에서 신규 주문·체결이 차단되는지 검증한다. [FR-05, FR-12, FR-14]
- 일손실 1%, MDD 5%, 지연, source divergence, clock drift, schema/config 불일치 킬스위치를 임계값 직전·정확히·초과 값으로 검사한다. [FR-14]
- 비용 2배와 유동성 절반 stress가 별도 scenario ID와 결과로 보존되는지 확인한다. [FR-12, FR-15, FR-16]

### 6.3 결정론·통계·승격 품질 게이트

- 고정 snapshot/config/commit/seed로 전체 E2E를 2회 실행해 이벤트 journal hash, 주문·체결 수, 비용, 최종 잔고, 지표가 일치해야 한다.
- 시간순 split 외의 무작위 shuffle을 금지한다. H1 기본 60/30/30과 확장 walk-forward, H2/H3 2026-07-10 이후 실제 구간을 강제한다.
- threshold, kappa, 보유시간은 train/validation에서만 선택한다. test 관측 후 변경은 새 strategy version과 미사용 test 구간 없이는 허용하지 않는다.
- 기본·각 비용 2배, 유동성 절반, 최고 하루·3일 제외 민감도, bootstrap CI, 날짜 permutation을 함께 보존한다.
- `PASS`는 PRD 10.6의 모든 기준을 충족할 때만 가능하다. 계산 불가·표본 부족은 `HOLD`, 하나라도 실패하면 `REJECT`다.

## 7. G-01~G-08 미결정사항 처리 방안과 착수 순서

### 7.1 공통 처리 방식

Phase 0 시작과 함께 각 gate에 `UNKNOWN/IN_REVIEW/CONFIRMED/REJECTED/EXPIRED` 상태를 부여하고 `docs/decisions/gates/G-xx.md` 결정 기록을 만든다. 기록에는 질문, 기능 범위, 공식 URL/문서 버전, 계정·시장 범위, 확인시각, 원본 증거 checksum, 담당 provider, 결론, 적용 config, 유효기간·재확인 조건, 미확인 기본동작을 둔다.

- 언론·SNS만으로 `CONFIRMED`로 바꾸지 않는다.
- 계정별 capability와 borrow는 다른 계정에 일반화하지 않는다.
- 확인 결과가 만료되면 자동으로 `EXPIRED`가 되고 관련 리스크 rule은 `BLOCK`한다.
- 시장 구조·API·규정 변경 감지 시 결정 기록을 덮어쓰지 않고 새 dated revision을 추가한다.
- gate가 전략의 축소 연구만 허용하면 결과에 `NON_EXECUTABLE` 또는 축소모델 버전을 표시한다.

### 7.2 권장 착수 순서

기반 경로는 `G-06 -> G-02 -> G-04 -> G-03 -> G-08` 순으로 처리한다. H2 feasibility 경로인 `G-01`과 `G-05`는 사용할 브로커 계정이 정해지고 G-02 probe가 끝나는 즉시 병행할 수 있다. `G-07`은 Phase 0에서 공식 규정·세금의 연구/페이퍼 비용 가정을 먼저 고정하고, 전문가 검토와 실거래 허가는 Phase 4의 별도 gate로 남긴다.

| 순서 | Gate | Phase 0 처리·결정 산출물 | 미결정/부정 시 계획 | 해소되어야 하는 시점 |
| --- | --- | --- | --- | --- |
| 1 | G-06 저장·자동수집·재배포 범위 | 공급자별 이용약관 URL·버전·확인일, raw/normalized 보관기간, 암호화·삭제·외부배포 정책을 catalog에 기록 | 허용이 확인된 최소 데이터만 로컬 보관, 외부 배포 금지; 불명확 dataset 수집 중지 | 어떤 실데이터의 지속 수집보다 먼저 |
| 2 | G-02 KIS/Toss capability·한도·token | 문서 capability matrix와 실제 조회 전용 probe를 계정별로 비교; venue/session/field/rate/subscription/token 결과 저장 | 지원한다고 가정하지 않고 fixture 연구만 진행; 필수 실시간 field 부재 전략은 비실행/축소 | Phase 0 완료 전 |
| 3 | G-04 국내 레버리지 상품 universe·공개시각·복제 | KRX/발행사 master를 대조해 instrument ID, 상장상태, PCF/AUM/NAV의 published/effective time, replication type 확정 | 동적 발견·공개시각·구조가 불명확한 상품을 H1에서 제외 | Phase 1 백필·feature 전 |
| 4 | G-03 종가 예상체결·프로그램·호가 깊이 | 공급자 필드 문서, 실제 sample, 시각·깊이·비용·라이선스 확인; 유료 데이터 구매 판단 기록 | 0으로 대체하지 않고 `missing` flag를 가진 H1 축소모델을 별도 version으로 평가 | H1 완전/축소모델 선택 전 |
| 5 | G-08 모의자본·기본 주문 크기 | 사용자 설정, 최소 거래단위, 실제 호가 깊이, 계좌 기준위험으로 baseline·stress size를 고정 | 단위 위험·bps·수익률 중심 보고, 절대 PnL로 승격하지 않음 | Phase 1 체결모델 보정 전 |
| 6A | G-01 ADR 전환 운영상태 | Citi/KSD 공식 공지와 실제 broker 접수 화면/서면 답변으로 방향, 최소수량, 비용, 처리기간, 접수 가능상태 확인 | `ConversionStatus=UNKNOWN`; H2 표시 premium 연구만 허용, 주문 의도 차단 | Phase 2 H2 실행 가능 실험 전 |
| 6B | G-05 SKHY 차입·대체헤지 | 사용할 broker의 유효 borrow quantity/rate/recall quote와 대체상품 설명·실시간 유동성 확인 | H2 신규 pair 차단; 단독 본주 long 금지 | Phase 2 H2 실행 가능 실험 전 |
| 7 | G-07 규제·세금·신고 | 구현 착수일 공식 규정에서 paper cost model에 필요한 세금·제약을 기록; 불명확 고위험 항목은 전문가 검토 backlog로 이관 | 보수적 비용 또는 비실행 처리, 실거래 승격은 무조건 금지 | 비용 기준은 Phase 2 전, 실거래는 Phase 4 전 |

G-01과 G-05 중 하나라도 미해결이면 H2 통계 연구 자체를 폐기하지는 않지만, 해당 결과는 `거래 가능 차익`이나 `PASS`로 승격할 수 없다. G-03 미해결은 H1 축소모델을 허용하되 완전모델과 성능을 합치지 않는다. G-08 미해결은 fill 결과를 단위 위험 기준으로만 해석한다.

## 8. 기능 요구사항 추적 요약

| FR | 주요 구현 위치 | 최초 완성 Phase | 핵심 수용 증거 |
| --- | --- | --- | --- |
| FR-01 | execution manifest, experiment registry | 0 | commit/config hash/snapshot가 모든 run에 존재 |
| FR-02 | provider registry, capability/license catalog | 0 | 공급자 역할·지연·이용조건과 probe 결과 조회 |
| FR-03 | raw recorder, cursor/checkpoint, ingestion | 0 | 중단·재개 후 중복 없음, raw checksum 불변 |
| FR-04 | domain contracts, normalizer, instrument master | 0 | 시간·통화·symbol·session·adjustment mapping test |
| FR-05 | quality/reconciliation, risk data gate | 0 | gap·stale·역순·중복·divergence 탐지와 차단 |
| FR-06 | calendar/session resolver | 0 | KRX/NXT/Nasdaq/HKEX 휴장·DST test |
| FR-07 | H2 fair/executable premium features | 2 | mid와 bid/ask 실행 괴리, 시각·FX·10:1 표시 |
| FR-08 | conversion/borrow registry, risk rules | 2 | 근거·유효 quote 없을 때 H2 order 0건 |
| FR-09 | H1 feature/strategy plugin | 1 | 사후 AUM/NAV 룩어헤드 canary 차단 |
| FR-10 | H2 strategy plugin | 2 | 두 다리·FX·전환·차입·전 비용 후 순괴리 |
| FR-11 | H3 feature/strategy plugin | 2 | 동시시장만 사용, 공통요인 통제 전후 비교 |
| FR-12 | event backtest, fill/cost models | 1, Phase 2 확장 | 부분체결·정지·가격제한·결정론 replay |
| FR-13 | PaperBrokerProvider, order state | 1, Phase 3 운영화 | 실제 주문 없이 주문·체결·잔고·PnL 이벤트 |
| FR-14 | risk engine, sizing, kill switch | 1, Phase 3 운영화 | 모든 intent에 ALLOW/BLOCK/REDUCE와 사유 |
| FR-15 | experiment/daily reports, promotion evaluator | 1, Phase 3 최종화 | 기본·2배 비용, 집중도, CI, 최종 판정 |
| FR-16 | lineage graph, event journal, manifests | 0, 전 Phase 확장 | signal에서 raw·버전, fill에서 signal까지 추적 |

## 9. 교차 단계 품질·운영 정책

- **스키마 변경**: backward-compatible 추가와 breaking change를 구분한다. breaking change는 schema version·migration·snapshot 재생 검증 없이 배포하지 않는다.
- **설정 변경**: 전략 임계값, 비용, risk limit, fill model 변경은 config hash와 결정 사유를 남기고 기존 실험을 덮어쓰지 않는다.
- **데이터 보정**: 기업행사·공급자 정정은 새 normalized version으로 기록하고 이전 snapshot을 재현 가능하게 유지한다.
- **공급자 불일치 해소**: `SOURCE_DIVERGENCE`로 차단된 구간은 장 종료 후 배치 대조 작업에서 PRD 7.2의 공급자 우선순위(공식 종가·기준정보는 KRX·발행사 우선)에 따라 권위 소스를 결정하고 새 normalized version으로 보정한다. 해소 근거(권위 소스, 대조 시각, 적용 tolerance, 원본 레코드 ID)를 lineage에 기록한 뒤에만 플래그를 해제하며, 해소 전 구간은 신호 생성·실험 입력에서 계속 차단 상태로 남는다. 권위 소스를 결정할 수 없으면 해당 구간을 영구 `EXCLUDED`로 표시하고 사유를 남긴다.
- **재처리**: raw ID 범위와 algorithm version을 지정한 idempotent job으로 실행하며 새 snapshot을 생성한다.
- **장애 격리**: 공급자별 수집 task, checkpoint, circuit 상태를 분리한다. 핵심 공급자가 죽으면 전략은 차단하되 다른 원시 수집·상태 보고는 계속한다.
- **시간 동기화**: OS clock drift를 측정하고 threshold 초과 시 실시간 신호를 막는다. 과거 replay는 기록된 event/received/published time에서 결정한 versioned `available_time_utc`만 사용한다.
- **보안**: CI secret scan, fixture scrubber, 로그 redaction test를 gate로 둔다. 조회 전용 키 외 credential은 v1에서 받지 않는다.
- **문서화**: provider 계약, 데이터 schema, gate 결정, 전략 version, 운영 runbook을 코드 변경과 같은 검토 단위로 갱신한다. 완료되지 않은 기능은 계획/보류로 표시한다.

## 10. 구현 착수 체크리스트

1. `prd.md`, `background.md`, `draft_prd.md`의 checksum을 기록하고 두 참고 원문을 보호 대상으로 지정한다.
2. G-06 데이터 이용조건 검토와 G-02 계정별 capability probe 범위를 먼저 확정한다.
3. Phase 0의 domain/port/schema를 수용 테스트에서 먼저 고정한다.
4. raw -> normalized -> lineage의 한 공급자 vertical slice를 fixture로 완성한 뒤 공급자를 늘린다.
5. Phase 1에서 H1 한 전략으로 event engine, risk, fill, report의 끝단까지 검증한다.
6. Phase 2에서 교차시장·두 다리·통화 기능을 추가하되 H1 결정론 회귀시험을 유지한다.
7. Phase 3 시작 전 실주문 adapter 부재, kill switch, 재시작 복구, daily report runbook을 점검한다.
8. 각 Phase 종료 시 FR trace, gate 상태, 테스트 결과, 남은 데이터·통계 한계를 함께 승인한다.

이 계획의 완료는 코드 작성 완료를 뜻하지 않는다. 각 Phase는 명시된 데이터 증거와 테스트를 통과해야 하며, 시장·API·규제에 관한 미결정사항은 G-01~G-08 결정 기록으로 해소되기 전까지 안전한 기본값을 유지한다.
