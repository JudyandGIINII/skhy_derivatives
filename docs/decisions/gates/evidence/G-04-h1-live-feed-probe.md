# G-04 H1 KIS/Toss live snapshot read-only probe

## 범위와 안전조치

- 실측시각: `2026-07-18T13:06:53Z`
- secret backend: macOS Keychain (`SKHY_SECRET_BACKEND=keychain`)
- 호출 범위: OAuth token, KIS/Toss 시세·NAV 조회용 HTTP GET
- 금지 범위: 계좌, 주문, 정정, 취소, 입출금 API는 호출하지 않음
- secret은 메모리에서만 사용했고 응답 필드명과 시세·시각만 정제해 기록함

## 실측 판정

| 공급자·endpoint | 실측 결과 | H1 사용 판정 |
| --- | --- | --- |
| KIS vps `domestic-stock/.../inquire-price` | `stck_prpr`, 전일가, OHLC, 누적거래량 등 80필드. 공급자 체결일자·시각과 `nav`/`inav`/`iiv`는 없음 | 현재가 가용성 확인에만 쓰고 이 응답만으로 fresh라고 판정하지 않음 |
| KIS vps `domestic-stock/.../inquire-time-itemchartprice` | `stck_bsop_date=20260716`, `stck_cntg_hour=151000`, `stck_prpr` 관측 | 기초주식의 lookahead-safe 관측시각·가격으로 통합 |
| KIS vps `etfetn/.../inquire-price` | ETF `0193T0`에서 `stck_prpr=14585`, `nav=14497.91`, `prdy_last_nav` 관측. 필드명 `inav`/`iiv`는 없음 | KIS가 라벨한 `NAV`로만 보존. 명시적 iNAV로 개명하지 않음 |
| KIS vps `etfetn/.../nav-comparison-time-trend` | 30건, `bsop_hour`, `nav`, `stck_prpr`, 괴리율·거래량 관측 | KIS 기초주식 분봉에서 확정한 거래일 + `bsop_hour`로 NAV 관측시각을 보존 |
| KIS prod | 현재 Keychain key로 `ProviderAccessDeniedError` | 현 계정으로는 실전 시장 live KIS feed 미확보. vps는 `SIMULATED`로 차단 |
| Toss prod `/api/v1/prices` | `000660`, `005930`, ETF `0193T0`, ETN `520101`의 `lastPrice`, timezone 포함 `timestamp`, `currency` 관측 | KIS 현재가 대조용. 공급자 timestamp로 freshness·time-skew 판정 |
| Toss prod `/api/v1/orderbook` | timezone 포함 `timestamp`, 10단계 `asks`/`bids`의 가격·잔량 관측 | 실제 양방향 `MarketQuote`로 매핑 가능 |

실측일은 토요일이어서 Toss가 반환한 최신 timestamp는 `2026-07-16`
장중·시간외 시각이었다. 이는 endpoint가 timestamp를 제공한다는 증거이지
`2026-07-18`에 fresh하다는 증거가 아니다. 구현된 guard는 이 응답을 `STALE`로
표시하고 H1 신호를 차단한다.

## 구현 결론

1. KIS 일반 현재가에 없는 timestamp를 client 벽시계로 만들지 않는다.
   기초주식은 KIS 분봉의 거래일·체결시각, ETF/ETN NAV는 KIS 분별
   `bsop_hour`를 쓴다. REST 수신시각은 게시·가용시각으로 별도 저장한다.
2. KIS `prod`만 `LIVE`, `vps`는 `SIMULATED`로 태그하고 원 H1에서 차단한다.
3. KIS·Toss의 공급자 관측시각 차이와 가격 괴리를 검사한다. KRX 직전일
   종가와는 일치를 요구하지 않고 종목별 일중 변동 bound를 넘는 이상치만
   `SOURCE_DIVERGENCE`로 차단한다.
4. KIS 분별 `nav`는 lineage로 보존하지만 H1 close-pressure 계산에는 직접
   사용하지 않는다. 실제 AUM 대신 직전일 KRX `NAV/IV × LIST_SHRS`
   listed-notional proxy를 계속 사용한다.
5. 이번 구현은 15:10 point-in-time REST snapshot까지다. 연속 websocket, 자동
   재연결, 15:10 scheduler, 세션 상태 feed는 Phase 2에 남긴다.

## 공식 자료

- KIS Open API 목록: https://apiportal.koreainvestment.com/apiservice-category
- KIS 공식 예제: https://github.com/koreainvestment/open-trading-api
- Toss OpenAPI source of truth: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json

`docs/decisions/gates/G-04.md`의 상태는 이 증거 작업에서 변경하지 않았다.
