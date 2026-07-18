"""PRD 8장 공통 데이터 계약에서 쓰이는 열거형."""

from __future__ import annotations

from enum import StrEnum


class Currency(StrEnum):
    """ISO 4217. v1 유니버스에 필요한 통화만 정의한다 (PRD 8.1)."""

    KRW = "KRW"
    USD = "USD"
    HKD = "HKD"


class Venue(StrEnum):
    KRX = "KRX"
    NXT = "NXT"
    NASDAQ = "NASDAQ"
    HKEX = "HKEX"
    OTC = "OTC"
    REFERENCE = "REFERENCE"


class AssetClass(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    ADR = "ADR"
    LEVERAGED_ETF = "LEVERAGED_ETF"
    LEVERAGED_ETN = "LEVERAGED_ETN"
    SWAP_PRODUCT = "SWAP_PRODUCT"
    STOCK_FUTURE = "STOCK_FUTURE"
    FX = "FX"


LEVERAGED_ASSET_CLASSES = frozenset(
    {AssetClass.LEVERAGED_ETF, AssetClass.LEVERAGED_ETN, AssetClass.SWAP_PRODUCT}
)


class Session(StrEnum):
    PRE = "PRE"
    REGULAR = "REGULAR"
    CLOSE_AUCTION = "CLOSE_AUCTION"
    AFTER = "AFTER"
    CLOSED = "CLOSED"
    REFERENCE = "REFERENCE"


class AdjustmentStatus(StrEnum):
    RAW = "RAW"
    ADJUSTED = "ADJUSTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class QualityFlag(StrEnum):
    """PRD 8.3 최소 지원 목록."""

    STALE = "STALE"
    DELAYED = "DELAYED"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    DUPLICATE = "DUPLICATE"
    GAP = "GAP"
    SOURCE_DIVERGENCE = "SOURCE_DIVERGENCE"
    MARKET_CLOSED = "MARKET_CLOSED"
    HALTED = "HALTED"
    UNADJUSTED_CORPORATE_ACTION = "UNADJUSTED_CORPORATE_ACTION"
    UNKNOWN_CONVERSION = "UNKNOWN_CONVERSION"
    BORROW_UNAVAILABLE = "BORROW_UNAVAILABLE"
    UNVERIFIED_SOCIAL_CLAIM = "UNVERIFIED_SOCIAL_CLAIM"


class MarketDataFeedMode(StrEnum):
    """시세 feed의 운영 모드.

    SIMULATED는 모의서버나 fixture를 뜻하며 원래 H1의 실시간 신호에
    입력할 수 없다.
    """

    LIVE = "LIVE"
    SIMULATED = "SIMULATED"


class ReplicationType(StrEnum):
    """PRD 8.2 FundSnapshot.replication_type."""

    PHYSICAL = "PHYSICAL"
    FUTURES = "FUTURES"
    SWAP = "SWAP"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class ConversionStatusValue(StrEnum):
    """PRD 5.2 전환 상태 열거형."""

    UNKNOWN = "UNKNOWN"
    ANNOUNCED = "ANNOUNCED"
    OPERATIONAL = "OPERATIONAL"
    SUSPENDED = "SUSPENDED"


class RiskDecisionType(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REDUCE = "REDUCE"


class PromotionVerdict(StrEnum):
    PASS = "PASS"
    HOLD = "HOLD"
    REJECT = "REJECT"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class TimeInForce(StrEnum):
    DAY = "DAY"
    IOC = "IOC"
    FOK = "FOK"


class OrderStatus(StrEnum):
    """PRD 4.6/`implementation_plan.md` 4.6 페이퍼 브로커 상태 전이."""

    CREATED = "CREATED"
    RISK_ACCEPTED = "RISK_ACCEPTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class SignalDirection(StrEnum):
    """전략마다 의미가 다르므로 일반화된 값만 정의한다.

    H1: LONG(상승압력 매수)/SHORT(하락압력 매도).
    H2: PAIR_LONG_COMMON_SHORT_ADR(기본 방향)/PAIR_SHORT_COMMON_LONG_ADR(음premium 변형).
    H3: LONG/SHORT (선행 시장 방향을 따르는 단일 다리).
    """

    LONG = "LONG"
    SHORT = "SHORT"
    PAIR_LONG_COMMON_SHORT_ADR = "PAIR_LONG_COMMON_SHORT_ADR"
    PAIR_SHORT_COMMON_LONG_ADR = "PAIR_SHORT_COMMON_LONG_ADR"
