# G-04 국내 단일종목 레버리지 universe probe 증거

## 판정 요약

- 조사일: 2026-07-18
- 최신 실측시각: 2026-07-18T10:26:47.996290Z
- 기준 거래일: 2026-07-16
- 판정: `IN_REVIEW` — 실제 universe는 발견했지만 G-04 전체 증거는 미완성
- 안전 범위: macOS Keychain의 `KRX_API_KEY`를 메모리에서만 읽어 KRX ETF/ETN
  단일 거래일 데이터를 조회했다. 주문·쓰기·계좌·백필 경로는 호출하지 않았고 secret
  값은 출력하거나 파일에 기록하지 않았다.

현재 구현은 KRX 일별 ETF/ETN 응답에서 단일종목 레버리지 상품 18개를 실제로 발견해
`InstrumentMaster`에 등록한다. 그러나 응답에 복제방식, PCF, NAV 게시시각이 없으므로
G-04를 `CONFIRMED`할 수는 없다.

## 1. 검증된 endpoint와 응답 범위

| 종류 | 검증된 HTTP GET 경로 | 파라미터 | 레코드 수 | 필드 수 |
| --- | --- | --- | ---: | ---: |
| ETF | `/svc/apis/etp/etf_bydd_trd` | `basDd=20260716` | 1,146 | 19 |
| ETN | `/svc/apis/etp/etn_bydd_trd` | `basDd=20260716` | 386 | 19 |

두 endpoint 모두 `AUTH_KEY` 헤더와 `OutBlock_1` 배열을 사용한다. 추정 후보였던
`/svc/apis/etf/etf_bydd_trd`, `/svc/apis/etn/etn_bydd_trd`는 실제 호출에서 HTTP
404였으므로 구현에 사용하지 않았다.

### ETF 관측 필드

`ACC_TRDVAL`, `ACC_TRDVOL`, `BAS_DD`, `CMPPREVDD_IDX`, `CMPPREVDD_PRC`,
`FLUC_RT`, `FLUC_RT_IDX`, `IDX_IND_NM`, `INVSTASST_NETASST_TOTAMT`, `ISU_CD`,
`ISU_NM`, `LIST_SHRS`, `MKTCAP`, `NAV`, `OBJ_STKPRC_IDX`, `TDD_CLSPRC`,
`TDD_HGPRC`, `TDD_LWPRC`, `TDD_OPNPRC`

### ETN 관측 필드

`ACC_TRDVAL`, `ACC_TRDVOL`, `BAS_DD`, `CMPPREVDD_IDX`, `CMPPREVDD_PRC`,
`FLUC_RT`, `FLUC_RT_IDX`, `IDX_IND_NM`, `INDIC_VAL_AMT`, `ISU_CD`, `ISU_NM`,
`LIST_SHRS`, `MKTCAP`, `OBJ_STKPRC_IDX`, `PER1SECU_INDIC_VAL`, `TDD_CLSPRC`,
`TDD_HGPRC`, `TDD_LWPRC`, `TDD_OPNPRC`

## 2. discovery·등록 규칙

`application/leverage_universe_discovery.py`는 다음 조건을 적용한다.

1. `ISU_NM`에 `단일종목` marker가 있는 행만 후보로 본다.
2. `레버리지`는 기본 `+2`, `인버스`는 기본 `-1`, `인버스2X`는 `-2`로 분류한다.
3. `IDX_IND_NM`에서 `KRX`, `TR`, `선물`, `레버리지`, `인버스`, `지수` marker를
   제거해 기초자산명을 추출한다.
4. ETF는 `LEVERAGED_ETF`, ETN은 `LEVERAGED_ETN`으로 분류하고
   `KRX_<종목코드>_<asset_class>` 형태의 안정 ID로 `InstrumentMaster`에 등록한다.
5. 필수 필드·배율·기초자산을 해석하지 못한 단일종목 행은 조용히 버리지 않고
   `LeveragedProductExclusion`으로 반환한다.

일별 응답에 존재한다는 사실은 해당 거래일에 데이터가 관측됐다는 뜻이다. API가
최초 상장일·상폐일·명시적 상태 필드를 제공하지 않으므로 `listed_at_utc`나
`delisted_at_utc`를 추정하지 않는다. 등록된 `is_active=True`는
`PRESENT_IN_DAILY_RESPONSE` snapshot 의미이지 공식 상장상태 이력의 대체물이 아니다.

## 3. 실제 발견 universe

실행 결과는 상품 18개, exclusion 0개, `InstrumentMaster` 등록 18개다. 구성은 ETF
16개·ETN 2개, 삼성전자 기초 9개·SK하이닉스 기초 9개다.

| 종류 | 종목코드 | 상품명 | 기초자산 | 배율 | NAV/지표가치 | 상장좌수 | 관측상태 |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| ETF | `0192L0` | RISE SK하이닉스단일종목레버리지 | SK하이닉스 | 2 | 12,415.79 | 5,800,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0192M0` | RISE 삼성전자단일종목레버리지 | 삼성전자 | 2 | 12,247.34 | 4,175,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0193K0` | PLUS 삼성전자단일종목레버리지 | 삼성전자 | 2 | 13,244.65 | 1,250,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0193L0` | PLUS 삼성전자선물단일종목인버스2X | 삼성전자 | -2 | 17,437.24 | 4,080,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0193T0` | KODEX SK하이닉스단일종목레버리지 | SK하이닉스 | 2 | 14,497.91 | 260,825,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0193W0` | KODEX 삼성전자단일종목레버리지 | 삼성전자 | 2 | 13,172.08 | 172,600,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0194M0` | ACE 삼성전자단일종목레버리지 | 삼성전자 | 2 | 12,184.74 | 5,575,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0194N0` | KIWOOM 삼성전자선물단일종목레버리지 | 삼성전자 | 2 | 11,965.28 | 900,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0194R0` | KIWOOM SK하이닉스선물단일종목레버리지 | SK하이닉스 | 2 | 11,888.37 | 1,475,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0194T0` | ACE SK하이닉스단일종목레버리지 | SK하이닉스 | 2 | 12,226.72 | 6,475,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0195R0` | TIGER 삼성전자단일종목레버리지 | 삼성전자 | 2 | 12,105.13 | 102,900,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0195S0` | TIGER SK하이닉스단일종목레버리지 | SK하이닉스 | 2 | 12,222.34 | 165,300,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0197W0` | SOL SK하이닉스단일종목레버리지 | SK하이닉스 | 2 | 12,087.80 | 6,000,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0197X0` | SOL SK하이닉스선물단일종목인버스2X | SK하이닉스 | -2 | 11,332.37 | 14,125,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0198B0` | 1Q 삼성전자선물단일종목레버리지 | 삼성전자 | 2 | 12,534.11 | 2,055,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETF | `0198D0` | 1Q SK하이닉스선물단일종목레버리지 | SK하이닉스 | 2 | 12,756.80 | 2,320,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETN | `520100` | 미래에셋 레버리지 삼성전자 단일종목 ETN | 삼성전자 | 2 | 12,412.61 | 5,000,000 | `PRESENT_IN_DAILY_RESPONSE` |
| ETN | `520101` | 미래에셋 레버리지 SK하이닉스 단일종목ETN | SK하이닉스 | 2 | 14,278.21 | 5,000,000 | `PRESENT_IN_DAILY_RESPONSE` |

## 4. 코드 경로

| 구성요소 | 갱신 후 동작 | 남은 한계 |
| --- | --- | --- |
| `adapters/providers/krx/client.py` | 주권·ETF·ETN 일별 read-only GET, `HISTORICAL_BARS`와 `INSTRUMENT_MASTER` capability 선언 | PCF·공식 master·게시시각 API는 없음 |
| `application/leverage_universe_discovery.py` | 단일종목 marker, 배율, 기초지수명을 검증하고 `InstrumentMaster`에 active snapshot 등록 | 이름·지수명 기반 분류이며 공식 상장일·상폐일을 만들지 않음 |
| `application/instrument_master.py` | 발견 결과를 `InstrumentRecord`로 보관 | 영속 master와 상태 변경 이력은 없음 |
| `discover_leveraged_products()` | `LEVERAGED_ETF/ETN/SWAP_PRODUCT`와 시점 유효성을 필터링 | fund snapshot이 없으면 H1 입력은 여전히 불완전 |
| `collect_fund_snapshots()` | 공개시각 등 필드가 누락된 snapshot은 exclusion 처리 | 실 KRX/발행사 `FUND_SNAPSHOT` provider는 아직 없음 |

## 5. 확보된 항목과 남은 gap

### 확보됨

- ETF/ETN endpoint 경로, `basDd`, 인증 헤더, 응답 배열과 필드
- 기준 거래일에 관측된 단일종목 상품 코드·이름·ETF/ETN 구분
- 상품명과 기초지수명에 근거한 삼성전자/SK하이닉스 연결 및 +2/-2 분류
- ETF NAV, ETN 1증권당 지표가치, 상장좌수와 기준일
- 18개 결과의 `InstrumentMaster` 등록 경로와 sanitized fixture 테스트

### 미확인

| 항목 | 일별 endpoint로 확정할 수 없는 이유 |
| --- | --- |
| 공식 상장상태·최초 상장일·상폐일 이력 | 응답에 상태·상장일·상폐일 필드가 없음. 당일 존재만 관측 가능 |
| 복제방식 `PHYSICAL/FUTURES/SWAP/MIXED` | 상품명·기초지수의 `선물`은 지수 특성 단서일 뿐 실제 운용 포트폴리오의 복제방식 증거가 아님 |
| PCF와 구성자산 | ETF/ETN 일별매매 응답에 PCF가 없음 |
| AUM의 정의·발행사 대조 | ETF `INVSTASST_NETASST_TOTAMT`는 관측되지만 발행사 공시와 정의·시각을 대조하지 않음 |
| NAV/iNAV의 `published_at`·`effective_at` | `BAS_DD`와 값은 있으나 실제 게시시각·기준시각 및 timezone 필드가 없음 |
| 이름 기반 목표배율의 공식 master 대조 | 현재 18개는 규칙과 공식 출시 합계가 일치하지만 목표배율 전용 필드·상품설명서 대조가 없음 |

## 6. G-04 해소 체크리스트

- [x] KRX ETF·ETN 일별 endpoint의 실제 경로·파라미터·응답 스키마를 read-only로 검증한다.
- [x] 단일종목 상품 18개를 발견하고 `LEVERAGED_ETF/ETN`으로 분류해
      `InstrumentMaster`에 등록한다.
- [x] 실제 응답을 정제한 ETF/ETN fixture와 endpoint·분류·등록 단위 테스트를 추가한다.
- [ ] KIND·KRX 공식 master·발행사 상품 목록으로 표준코드, 최초 상장일, 상폐·정지
      상태, 목표배율을 종목별 대조한다.
- [ ] ETF PCF/구성종목과 ETN 투자설명서 원문을 확보하고 raw checksum·이용조건을 보존한다.
- [ ] AUM·NAV/iNAV·발행좌수의 실제 `published_at`과 `effective_at`을 분리할 수 있는
      공식 게시시각·timezone 규칙을 확인한다.
- [ ] 복제방식을 투자설명서·신탁계약·PCF에서 종목별로 확인해
      `PHYSICAL/FUTURES/SWAP/MIXED` 근거를 저장한다.
- [ ] 공개시각·복제방식 누락 또는 source 불일치 상품이 `FundSnapshotExclusion`으로
      차단되는 실데이터 계약 테스트를 추가한다.
- [ ] 위 항목을 모두 충족하기 전에는 G-04를 `CONFIRMED`로 변경하지 않고 KRX 백필을
      실행하지 않는다.

## 7. 공식 근거

1. [KRX Open API 서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)은
   주권 일별정보와 별도로 ETF·ETN 일별매매정보를 제공한다고 명시한다.
2. [금융위원회 2026-05-26 보도자료](https://www.fsc.go.kr/no010101/86973)는
   2026-05-27 삼성전자·SK하이닉스 기초 ETF 16개·ETN 2개 출시를 설명한다.
3. [금융위원회 2026-07-16 보도자료](https://www.fsc.go.kr/no010101/87353)는
   시장 안정화 전까지 단일종목 관련 신규 상장을 잠정 중단한다고 밝힌다.

## 결론

현재 KRX read-only API와 구현으로 기준 거래일의 국내 단일종목 레버리지 universe
18개는 재현 가능하게 발견·등록된다. 하지만 G-04는 universe만 묻지 않고 복제방식과
PCF·AUM/NAV 공개시각까지 요구한다. 이 필수 증거들이 남아 있으므로 상태는
`IN_REVIEW`를 유지하고 H1의 불완전 상품 및 KRX 백필 차단을 계속 적용한다.
