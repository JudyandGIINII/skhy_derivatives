# G-04 국내 단일종목 레버리지 universe probe 증거

## 판정 요약

- 조사일: 2026-07-18
- 실측시각: 2026-07-18T10:07:52.680826Z
- 판정: `IN_REVIEW` — 현재 KRX 조회 경로로 G-04를 `CONFIRMED`할 수 없음
- 안전 범위: macOS Keychain의 `KRX_API_KEY`를 메모리에서만 읽어 KRX 일별 주식
  시세를 조회했다. 주문·쓰기·계좌·백필 경로는 호출하지 않았고 secret 값은 출력하거나
  파일에 기록하지 않았다.

현재 구현은 SK하이닉스 본주의 KOSPI 일별 OHLCV를 확인할 수 있지만, ETF/ETN
universe, PCF·AUM·NAV 공개시각, 복제방식을 확인하는 reference-data 경로는 아니다.

## 1. 현재 코드 경로

| 구성요소 | 현재 동작 | G-04에 필요한 입력 | 현재 gap |
| --- | --- | --- | --- |
| `adapters/providers/krx/client.py` | `/svc/apis/sto/stk_bydd_trd`에서 유가증권시장 주권의 일별매매정보 조회 | ETF/ETN master, 기초자산, 목표배율, 상장상태 | `HISTORICAL_BARS`만 선언하며 `INSTRUMENT_MASTER`·`FUND_SNAPSHOT` capability가 없음 |
| `application/instrument_master.py` | 호출자가 등록한 `InstrumentRecord`와 symbol alias를 인메모리에서 보관 | 안정 `instrument_id`, `asset_class`, 상장·상폐시각, 원천 symbol | KRX/발행사 데이터로 master를 채우는 실 loader/provider가 없음 |
| `discover_leveraged_products()` | `InstrumentMaster.list_instruments()`에서 `asset_class`가 `LEVERAGED_ETF`, `LEVERAGED_ETN`, `SWAP_PRODUCT`이고 해당 시점에 상장 유효한 ID만 반환 | 신뢰 가능한 `asset_class`, `listed_at_utc`, `delisted_at_utc` | 분류값을 스스로 추론하지 않으므로 upstream master가 없으면 결과가 비어 있음 |
| `collect_fund_snapshots()` | 발견된 ID마다 `ReferenceDataProvider.get_fund_snapshot()` 호출; 실패·필드 누락은 exclusion으로 기록 | 배율, AUM, NAV/iNAV, 발행좌수, 복제방식, `published_at`, `effective_at` | 실 KRX/발행사 `FUND_SNAPSHOT` provider가 없고 fixture만 존재 |

`InstrumentRecord.asset_class`는 현재 KRX 일별시세 응답에서 자동으로 채워지지 않는다.
실제 universe 발견을 위해서는 별도 reference-data adapter가 ETF/ETN master를 읽어
`InstrumentRecord`를 등록한 후에야 `discover_leveraged_products()`가 의미 있는 결과를 낸다.

## 2. KRX read-only 실측

### 호출 범위

- endpoint: `/svc/apis/sto/stk_bydd_trd`
- 요청 거래일: 2026-07-17(빈 응답), 이어서 2026-07-16(데이터 확인)
- 반환 시장: `KOSPI`
- 반환 레코드: 944건
- SK하이닉스 종목명 일치: 1건(`SK하이닉스`)
- 상품명에서 `레버리지`, `인버스`, `ETF`, `ETN` 키워드 일치: 0건

### 관측 필드 15개

`ACC_TRDVAL`, `ACC_TRDVOL`, `BAS_DD`, `CMPPREVDD_PRC`, `FLUC_RT`, `ISU_CD`,
`ISU_NM`, `LIST_SHRS`, `MKTCAP`, `MKT_NM`, `SECT_TP_NM`, `TDD_CLSPRC`,
`TDD_HGPRC`, `TDD_LWPRC`, `TDD_OPNPRC`

이 응답으로 확인할 수 있는 것은 주권 종목코드·종목명, 일별 OHLCV, 거래대금,
상장주식수, 시가총액이다. ETF/ETN 여부, 기초자산, 목표배율, PCF, AUM, NAV/iNAV,
값의 공개시각, 복제방식 필드는 없다.

키워드 일치 0건만으로 시장에 레버리지 상품이 없다고 결론내리지는 않는다. KRX 공식
서비스 목록이 유가증권 주권 일별정보와 ETF·ETN 일별정보를 서로 다른 API로 분리하고
있으며, 현재 client는 전자만 호출한다. 따라서 이 결과는 "상품 부재"가 아니라
"현재 endpoint의 universe 식별 범위 밖"이라는 증거다.

## 3. 공식 자료로 확인된 사실

1. [금융위원회 2026-05-26 보도자료](https://www.fsc.go.kr/no010101/86973)는
   2026-05-27 삼성전자·SK하이닉스 기초 ±2배 ETF/ETN 출시를 알리고, SK하이닉스
   기준 ETF 8개와 ETN 1개라는 합계 수준을 제시한다.
2. [금융위원회 2026-07-16 보도자료](https://www.fsc.go.kr/no010101/87353)는
   시장 안정화 전까지 단일종목 관련 신규 상장을 잠정 중단한다고 밝힌다.
3. [KRX Open API 서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)은
   `유가증권 일별매매정보`와 별도로 `ETF 일별매매정보`, `ETN 일별매매정보`를
   제공한다고 명시한다. 현재 repository client에는 ETF/ETN 전용 호출이 없다.
4. KRX의 `ETN 일별매매정보` 공식 페이지는 ETN 매매정보를 별도 증권상품 API로
   설명한다: <https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES003_S2.cmd?BO_ID=VujebrcOsZQMybnUuwLk>.

위 자료는 출시·정책 및 별도 데이터 경로의 존재를 확인하지만, G-04가 요구하는
현재 정확한 종목별 metadata와 공개시각·복제방식 전체를 확정하지 않는다.

## 4. 아직 확인되지 않은 항목과 이유

| 미확인 항목 | 현재 경로로 불가능한 이유 |
| --- | --- |
| SK하이닉스 단일종목 레버리지 ETF/ETN의 정확한 현재 종목코드·종목명·상장상태 | 주권 일별 endpoint에는 ETF/ETN 레코드가 없고 ETF/ETN master adapter도 없음 |
| 각 상품의 목표배율과 기초자산 연결 | 응답에 기초자산·배율 필드가 없음 |
| 복제방식 `PHYSICAL/FUTURES/SWAP/MIXED` | 일별 매매정보가 운용 포트폴리오나 투자설명서 구조를 제공하지 않음 |
| PCF와 발행좌수, AUM, NAV/iNAV | 현재 15개 필드에 해당 값이 없음 |
| 각 값의 `published_at`과 `effective_at` | 응답은 기준일 `BAS_DD`만 제공하며 실제 게시·수신시각을 제공하지 않음 |

## 5. G-04 해소 체크리스트

- [ ] KRX 계정에서 ETF·ETN 전용 Open API의 정확한 API ID·HTTP endpoint·승인 범위를
      공식 개발명세로 확인하고 read-only adapter에 통합한다.
- [ ] ETF/ETN master에서 표준코드·단축코드·종목명·기초자산·목표배율·발행사·상장일·
      상폐일을 수집해 안정 `instrument_id`와 `InstrumentRecord.asset_class`를 채운다.
- [ ] KRX master와 KIND 신규상장·상장폐지 공시, 발행사 상품 목록을 대조해
      SK하이닉스 단일종목 universe를 종목별로 확정한다.
- [ ] 2026-07-16 신규상장 잠정 중단 이후의 신규·상폐·명칭 변경을 매 거래일 재확인한다.
- [ ] ETF PCF/구성종목, AUM, NAV/iNAV, 발행좌수의 공식 원천을 KRX 또는 각 발행사에서
      확보하고 raw 원문·이용조건·checksum을 보존한다.
- [ ] 웹페이지 표시시각이 아니라 실제 공식 게시시각을 `published_at`, 값의 기준시각을
      `effective_at`으로 분리할 수 있는 원천과 timezone 규칙을 문서화한다.
- [ ] 복제방식을 투자설명서·신탁계약·발행사 PCF에서 종목별로 확인하고
      `PHYSICAL/FUTURES/SWAP/MIXED/UNKNOWN` 매핑 근거를 저장한다.
- [ ] 공개시각·복제방식이 누락되거나 source 간 불일치한 상품이
      `FundSnapshotExclusion`으로 차단되는 실데이터 계약 테스트를 추가한다.
- [ ] 종목별 증거 URL, 확인시각, checksum, 담당 provider, 유효기간을 gate 결정에 기록한다.
- [ ] 위 항목을 모두 충족하기 전에는 G-04를 `CONFIRMED`로 변경하지 않고 KRX 백필을
      실행하지 않는다.

## 결론

확인된 것은 현재 KRX 키가 유가증권 주권 일별 데이터를 read-only로 반환한다는 점,
SK하이닉스 본주가 그 응답에 존재한다는 점, 그리고 KRX가 ETF/ETN 일별정보를 별도
서비스로 제공한다는 점이다. 미확인인 것은 H1에 필요한 실제 상품별 universe,
PCF·AUM·NAV의 공개·기준시각, 복제방식이다. 현재 client와 증거만으로는 이 gap을
메울 수 없으므로 G-04는 계속 차단 상태여야 한다.
