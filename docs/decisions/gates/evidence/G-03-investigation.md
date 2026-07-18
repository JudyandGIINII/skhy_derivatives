# G-03 게이트 조사 증거 문서 (G-03 Investigation Evidence)
> **조사일시:** 2026-07-19T08:10:23+09:00  
> **대상 API:** 한국투자증권 KIS Developers Open API, 토스증권 Open API  

## 1. KIS 및 토스증권 Open API 필드별 상세 분석

### A. 종가 예상체결가 및 예상체결수량, 불균형 (Imbalance)
*   **한국투자증권 (KIS Open API):**
    *   **조회형 (REST API):** `/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn` (TR_ID: `FHKST01010200`) 엔드포인트를 통해 예상체결가(`antc_cnpr`) 및 예상체결수량(`antc_vol`) 조회가 가능합니다.
    *   **실시간 수신 (WebSocket):** 웹소켓 실시간 호가/예상체결 등록 (`TR_ID: H0STASP0`)을 구독하면 15:20 ~ 15:30 단일가 매매 시간 동안 실시간 예상체결 데이터를 수신할 수 있습니다.
    *   **예상체결 불균형 (Imbalance):** KIS API는 예상 체결 가격/수량 및 총 매도/매수 잔량은 제공하지만, 매수/매도 미체결 불균형 수량(Imbalance Size/Direction)을 직접 계산한 단일 필드는 제공하지 않습니다. 총 잔량 차이를 통해 간접적으로 추정해야 합니다.
    *   **한도 및 지연:** REST 호출 한도는 실계좌 초당 20회, 모의계좌 초당 10회입니다. WebSocket은 지연시간이 밀리초(ms) 단위로 매우 짧습니다.
    *   **근거 URL:** [KIS Developers 국내주식 시세 API 가이드](https://apiportal.koreainvestment.com/)
*   **토스증권 (Toss Securities Open API):**
    *   **조회형 (REST API):** `/api/v1/orderbook` 엔드포인트를 통해 실시간 호가 정보를 제공합니다.
    *   **예상체결 지원 여부:** 토스증권 API는 일반적인 실시간 호가 및 체결 데이터(`GET /api/v1/prices`, `GET /api/v1/trades`)를 제공하지만, 단일가 매매 시간대의 **예상체결가 및 예상체결수량, 예상체결 불균형 정보를 공식적으로 지원하지 않습니다.**
    *   **근거 URL:** [토스증권 개발자 센터](https://developers.tossinvest.com/)

### B. 프로그램매매 순매수 (종목별 · 실시간)
*   **한국투자증권 (KIS Open API):**
    *   **조회형 (REST API):** `/uapi/domestic-stock/v1/quotations/investor-program-trade-today` 엔드포인트를 통해 당일 프로그램 매매 동향 조회가 가능합니다.
    *   **실시간 수신 (WebSocket):** 실시간 프로그램매매 데이터를 웹소켓(`TR_ID: H0NXPGM0`)을 통해 실시간으로 수신할 수 있습니다.
    *   **한도 및 지연:** REST API는 호출 한도 내에서 분/초 단위 조회가 가능하며, WebSocket 실시간 전송은 거래소의 공시 주기(수 초 단위)를 따릅니다.
    *   **근거 URL:** [KIS Developers 실시간 프로그램매매 API 가이드](https://apiportal.koreainvestment.com/)
*   **토스증권 (Toss Securities Open API):**
    *   **지원 여부:** 토스증권 API는 개인 투자자의 기본 매매 기능 및 시세 조회에 초점을 맞추고 있어, **종목별 실시간 프로그램 매매 데이터를 제공하지 않습니다.**
    *   **근거 URL:** [토스증권 개발자 센터](https://developers.tossinvest.com/)

### C. 호가 10단계 잔량 (Order Book Depth)
*   **한국투자증권 (KIS Open API):**
    *   **지원 여부:** REST API (`FHKST01010200`) 및 WebSocket (`H0STASP0`) 모두 10단계 매도/매수 호가와 각각의 잔량(Depth)을 완전하게 제공합니다.
*   **토스증권 (Toss Securities Open API):**
    *   **지원 여부:** `/api/v1/orderbook` 엔드포인트를 통해 실시간 호가 및 잔량 데이터를 제공하지만, 단일가 예상 체결 단계에서의 예상 호가 결합 형태는 누락되어 있습니다.

---

## 2. 유료 보완 경로 및 비용 검토 (KOSCOM / KRX)
무료 API의 예상체결 불균형 수량 부재 및 토스증권의 피드 누락을 공식 보완하기 위한 유료 대안은 다음과 같습니다.

*   **KOSCOM 실시간 시세 Feed (Koscom STP):**
    *   **내용:** 거래소 원천 데이터(예상체결 불균형, 프로그램매매 실시간 원장 등)를 다이렉트로 수신 가능.
    *   **비용:** 인프라 설치비 및 회선 비용 제외, 순수 실시간 정보 이용료만 **월 1,000,000 KRW ~ 5,000,000 KRW** 이상 소요.
    *   **라이선스:** 개인 투자자용 실시간 raw 피드 공급계약은 일반법상 허용되지 않으며, 적격 금융기관 또는 정보사업자 라이선스가 필요함.
*   **KRX 정보데이터시스템:**
    *   **내용:** 일별 프로그램매매 및 단일가 내역을 배치(Batch) 형태로 다운로드 가능.
    *   **비용:** 과거 데이터 백필용으로는 수십만 원 선에서 구매 가능하나, 15:10 실시간 H1 신호 생성용으로는 사용 불가.

---

## 3. PRD 7.1 원칙에 따른 결론 및 권고
*   **PRD 7.1 원칙:** 무료 KIS/Toss 피드로만 실시간 입력 구성하며, 미지원 필드를 위해 유료 데이터로 대체/보완하지 않음. 필수 실시간 필드 부재 시 축소 모델 또는 비실행 상태를 강제함.
*   **분석 결론:** 
    1.  **예상체결 불균형 데이터의 실시간 획득 불가능:** 무료 KIS/Toss API 모두 원천적인 예상체결 매수/매도 불균형 잔량(Imbalance size)을 단일 필드로 완벽하게 제공하지 못하며, 토스증권은 예상체결가/수량 및 프로그램매매 피드가 아예 누락되어 있습니다.
    2.  **유료 보완 불가:** 월 수백만 원에 달하는 KOSCOM/KRX 피드는 PRD 7.1 원칙에 의거하여 도입이 전면 금지됩니다.
*   **권고 사항:**
    *   원래 15:10 KST 기준의 H1 완전 모델(Full Model)은 필수 입력값 결측으로 인해 실행이 불가능합니다.
    *   따라서 H1 전략은 G-04에서 승인된 일별 프록시 데이터(`listed_notional_proxy` 등)를 사용하는 **축소 모델(`reduced_model`, 예: `h1_krx_daily_proxy_reduced_v1`)에 머무르는 것이 타당**하며, 실거래나 완전 모델로의 승격은 금지되어야 합니다.
