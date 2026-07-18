# G-04 KRX 상세·PCF·NAV 공개시각 probe 증거

## 판정 요약

- 조사일: 2026-07-18
- 실측시각: 2026-07-18T10:58:25.849553Z
- 기준 거래일: 2026-07-16
- 판정: KRX Open API로 날짜별 NAV·IV·기초지수명/종가까지는 확보할 수 있지만,
  PCF·구성종목·복제방식·정확한 공개시각은 제공되지 않는다.
- 안전 범위: `SKHY_SECRET_BACKEND=keychain`에서 `KRX_API_KEY`를 메모리로만 읽고
  HTTP GET만 실행했다. 주문·쓰기·계좌·백필은 호출하지 않았고 secret 값과 upstream
  오류 메시지는 파일에 기록하지 않았다.
- 정제 실측 snapshot: `G-04-krx-detail-probe.json`
- snapshot SHA-256:
  `e86d182f2432e791afa7d8d2cef3e95534896fbbe70c9bd2e455d79c79d7c7d3`

위 checksum은 이 Markdown의 자기참조 hash가 아니라, 함께 저장한 UTF-8 JSON
snapshot 파일 전체 바이트의 SHA-256이다.

## 1. 공식 서비스 범위 조사

[KRX Open API 서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)의
증권상품 범위는 다음 세 서비스뿐이다.

1. ETF 일별매매정보
2. ETN 일별매매정보
3. ELW 일별매매정보

목록에는 ETF/ETN 상세정보, 종목기본정보, PCF/PDF, 구성종목, 투자설명서, NAV/iNAV
게시시각 API가 없다. 지수 범위에도 KRX/KOSPI/KOSDAQ/채권/파생상품지수의
**일별시세정보**만 있고 지수 구성종목·산출시각 상세 서비스는 없다.

2026-06-01자
[KRX Open API 미제공 데이터 안내](https://openapi.krx.co.kr/contents/OPP/COMM/notice/OPPCOMM001_S2.cmd?bbsSeq=5)는
Open API가 서비스 목록의 데이터 항목만 제공하며 목록 밖 데이터는 Data Marketplace의
화면 검색·다운로드 또는 데이터 구매를 이용하라고 명시한다. 따라서 목록에 없는
PCF·상세정보 endpoint가 존재한다고 가정하거나 비공식 경로를 탐색하지 않았다.

## 2. 실제 endpoint 응답

| 서비스 | GET 경로 | 결과 | 레코드·필드 |
| --- | --- | --- | --- |
| ETF 일별매매정보 | `/svc/apis/etp/etf_bydd_trd?basDd=20260716` | HTTP 200 | 1,146건·19필드 |
| ETN 일별매매정보 | `/svc/apis/etp/etn_bydd_trd?basDd=20260716` | HTTP 200 | 386건·19필드 |
| KRX 시리즈 일별시세정보 | `/svc/apis/idx/krx_dd_trd?basDd=20260716` | HTTP 401 | 현재 키의 서비스 승인 범위 밖; 데이터 행 미수신 |

ETF/ETN 응답에서 `단일종목` 상품은 이전 조사와 동일하게 ETF 16개·ETN 2개였다.
두 응답의 전체 field name을 검사한 결과 PCF/구성종목 계열 필드와 공개·발표시각 계열
필드는 각각 0개였다.

KRX 지수 일별 API는 공식 서비스 목록에는 있지만 현재 Keychain 키로는 HTTP 401이므로
실응답 schema나 단일종목 기초지수 포함 여부를 검증하지 못했다. 또한 서비스 설명
자체가 일별시세정보이므로 PCF·복제방식·NAV 공개시각을 제공한다는 근거도 없다. 이
상태에서 client method를 추가하면 검증되지 않은 capability를 선언하게 되므로 통합하지
않았다.

## 3. 필드 의미와 시각 해석

공식 ETF/ETN 개발명세와 실제 응답에서 확인된 필드는 다음과 같다.

| 상품 | 필드 | 공식 표시명 | 프로그램적으로 말할 수 있는 범위 |
| --- | --- | --- | --- |
| ETF | `BAS_DD` | 기준일자 | 일자 수준의 데이터 기준일. 시각·timezone은 없음 |
| ETF | `NAV` | 순자산가치(NAV) | 기준일의 NAV 값 |
| ETF | `INVSTASST_NETASST_TOTAMT` | 순자산총액 | AUM 후보 필드이나 대상 16개가 모두 `0`이라 실제 AUM으로 채택 불가 |
| ETF | `IDX_IND_NM` | 기초지수_지수명 | 기초지수 명칭 |
| ETF | `OBJ_STKPRC_IDX` | 기초지수_종가 | 기준일의 기초지수 종가 |
| ETN | `BAS_DD` | 기준일자 | 일자 수준의 데이터 기준일. 시각·timezone은 없음 |
| ETN | `PER1SECU_INDIC_VAL` | 지표가치(IV) | 기준일의 ETN 1증권당 지표가치; 공식 명칭은 iNAV가 아니라 IV |
| ETN | `INDIC_VAL_AMT` | 지표가치총액 | 대상 2개가 모두 `0`이라 유효 총액으로 채택 불가 |
| ETN | `IDX_IND_NM` | 기초지수_지수명 | 기초지수 명칭 |
| ETN | `OBJ_STKPRC_IDX` | 기초지수_종가 | 기준일의 기초지수 종가 |

`BAS_DD=20260716`은 날짜 단위의 기준일 증거다. 응답에는 `published_at`, 실제 게시시각,
산출 완료시각, timezone, 장중 iNAV timestamp가 없다. 이번 probe의 HTTP 수신시각은
`2026-07-18T10:58:25.849553Z`이지만 이는 조사자의 `received_at`일 뿐 NAV/IV의
`published_at`이 아니다. 따라서 `BAS_DD`를 자정이나 장 마감시각으로 임의 변환하지
않는다.

## 4. 18개 상품 실측값

모든 행의 `BAS_DD`는 `20260716`이다. `순자산/지표총액`은 ETF의
`INVSTASST_NETASST_TOTAMT`, ETN의 `INDIC_VAL_AMT` 원문 값이다.

| 유형 | 코드 | 상품명 | NAV/IV | 순자산/지표총액 | 기초지수명 | 기초지수 종가 |
| --- | --- | --- | ---: | ---: | --- | ---: |
| ETF | `0198D0` | 1Q SK하이닉스선물단일종목레버리지 | 12,756.80 | 0 | KRX SK하이닉스 선물 지수 | 10,357.97 |
| ETF | `0198B0` | 1Q 삼성전자선물단일종목레버리지 | 12,534.11 | 0 | KRX 삼성전자 선물 지수 | 4,540.45 |
| ETF | `0194T0` | ACE SK하이닉스단일종목레버리지 | 12,226.72 | 0 | KRX SK하이닉스 지수 | 10,759.35 |
| ETF | `0194M0` | ACE 삼성전자단일종목레버리지 | 12,184.74 | 0 | KRX 삼성전자 지수 | 4,775.28 |
| ETF | `0194R0` | KIWOOM SK하이닉스선물단일종목레버리지 | 11,888.37 | 0 | KRX SK하이닉스 선물 지수 | 10,357.97 |
| ETF | `0194N0` | KIWOOM 삼성전자선물단일종목레버리지 | 11,965.28 | 0 | KRX 삼성전자 선물 지수 | 4,540.45 |
| ETF | `0193T0` | KODEX SK하이닉스단일종목레버리지 | 14,497.91 | 0 | KRX SK하이닉스 지수 | 10,759.35 |
| ETF | `0193W0` | KODEX 삼성전자단일종목레버리지 | 13,172.08 | 0 | KRX 삼성전자 지수 | 4,775.28 |
| ETF | `0193K0` | PLUS 삼성전자단일종목레버리지 | 13,244.65 | 0 | KRX 삼성전자 지수 | 4,775.28 |
| ETF | `0193L0` | PLUS 삼성전자선물단일종목인버스2X | 17,437.24 | 0 | KRX 삼성전자 선물 지수 | 4,540.45 |
| ETF | `0192L0` | RISE SK하이닉스단일종목레버리지 | 12,415.79 | 0 | KRX SK하이닉스 지수 | 10,759.35 |
| ETF | `0192M0` | RISE 삼성전자단일종목레버리지 | 12,247.34 | 0 | KRX 삼성전자 지수 | 4,775.28 |
| ETF | `0197W0` | SOL SK하이닉스단일종목레버리지 | 12,087.80 | 0 | KRX SK하이닉스 지수 | 10,759.35 |
| ETF | `0197X0` | SOL SK하이닉스선물단일종목인버스2X | 11,332.37 | 0 | KRX SK하이닉스 선물 지수 | 10,357.97 |
| ETF | `0195S0` | TIGER SK하이닉스단일종목레버리지 | 12,222.34 | 0 | KRX SK하이닉스 지수 | 10,759.35 |
| ETF | `0195R0` | TIGER 삼성전자단일종목레버리지 | 12,105.13 | 0 | KRX 삼성전자 지수 | 4,775.28 |
| ETN | `520101` | 미래에셋 레버리지 SK하이닉스 단일종목ETN | 14,278.21 | 0 | KRX SK하이닉스 TR 레버리지 지수 | 52,061.96 |
| ETN | `520100` | 미래에셋 레버리지 삼성전자 단일종목 ETN | 12,412.61 | 0 | KRX 삼성전자 TR 레버리지 지수 | 14,616.53 |

## 5. 복제방식 판단 한계

기초지수명 marker는 다음과 같이 관측됐다.

- `선물` 포함 ETF: 6개
- `TR` 포함 ETN: 2개
- 그 밖의 단일종목 지수명: ETF 10개

이는 **무엇을 추종하는 지수인가**에 대한 단서일 뿐, 펀드가 실제로 현물·선물·스왑 중
무엇을 보유해 지수를 복제하는지 보여 주지 않는다. 예를 들어 이름에 `선물`이 있어도
운용 포트폴리오가 선물만으로 구성됐다고 확정할 수 없고, `TR`은 total return 지수
표기이지 swap 복제의 증거가 아니다. 따라서 이 값만으로
`PHYSICAL/FUTURES/SWAP/MIXED`를 채우지 않는다.

## 6. 확보 가능·불가능 항목

### KRX Open API로 확보 가능

- 18개 상품의 기준일, 종목코드·종목명
- ETF NAV, ETN 1증권당 IV
- 기초지수명과 기준일 기초지수 종가
- 상장좌수
- ETF 순자산총액·ETN 지표가치총액이라는 필드의 존재

### 현재 응답에서 실사용 불가 또는 미제공

- 실제 AUM/지표가치총액: 관련 필드는 있으나 18개 모두 `0`
- 날짜보다 정밀한 NAV/IV `effective_at`: 시각·timezone이 없음
- NAV/IV `published_at`: 게시·발표·산출 완료시각 필드가 없음
- 장중 iNAV와 timestamp: Open API 서비스 목록과 응답에 없음
- PCF/PDF·구성종목·보유수량·비중: Open API 서비스 목록과 응답에 없음
- 공식 복제방식: 지수명만으로는 확정 불가
- 별도 ETF/ETN 상세정보: Open API 서비스 목록에 없음
- KRX 지수 일별 실응답: 서비스는 목록에 있으나 현재 키가 HTTP 401

## 결론

기존 `KrxReadOnlyClient`의 ETF/ETN 일별 메서드가 KRX Open API에서 G-04에 활용할
수 있는 범위를 이미 모두 수집하고 있다. 새로 검증된 상세·PCF·공개시각 endpoint는
없었으며, 현재 키로 KRX 지수 일별 endpoint도 실응답을 확보하지 못했다. 그러므로
검증되지 않은 client method나 capability는 추가하지 않았다.

KRX Open API만으로는 G-04의 PCF·복제방식·`published_at`을 해소할 수 없다. 다음
근거는 KRX Data Marketplace 화면 다운로드·구매 데이터 또는 발행사 PCF·투자설명서
조사에서 확보해야 한다. 이 문서는 기술적 증거만 추가하며 `G-04.md`의 상태 필드는
변경하지 않는다.
