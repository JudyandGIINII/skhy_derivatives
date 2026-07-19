"""KRX ETF/ETN 일별 응답에서 단일종목 레버리지 universe를 동적으로 등록한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from skhy_research.application.instrument_master import InstrumentMaster
from skhy_research.domain.enums import AssetClass, Venue
from skhy_research.domain.instrument import InstrumentRecord

_SINGLE_STOCK_MARKER = "단일종목"
_INDEX_NOISE_TOKENS = frozenset({"KRX", "TR", "레버리지", "인버스", "2X", "선물", "지수"})


class KrxEtpDailyReader(Protocol):
    def fetch_daily_etf_trades(self, trading_date: date) -> list[dict[str, Any]]: ...

    def fetch_daily_etn_trades(self, trading_date: date) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class DiscoveredLeveragedProduct:
    instrument_id: str
    source_symbol: str
    display_name: str
    asset_class: AssetClass
    underlying_name: str
    leverage_factor: Decimal
    basis_date: date
    reference_index_name: str
    nav_or_indicative_value: Decimal | None
    listed_shares: Decimal | None
    observation_status: str = "PRESENT_IN_DAILY_RESPONSE"


@dataclass(frozen=True)
class LeveragedProductExclusion:
    asset_class: AssetClass
    source_symbol: str | None
    display_name: str | None
    reason: str


@dataclass(frozen=True)
class LeverageUniverseDiscoveryResult:
    basis_date: date
    products: tuple[DiscoveredLeveragedProduct, ...]
    exclusions: tuple[LeveragedProductExclusion, ...]


def discover_and_register_krx_leveraged_universe(
    client: KrxEtpDailyReader,
    master: InstrumentMaster,
    trading_date: date,
    *,
    target_underlyings: frozenset[str] | None = None,
) -> LeverageUniverseDiscoveryResult:
    """단일종목 marker와 기초지수명을 검증해 현재 관측된 ETF/ETN을 master에 등록한다.

    일별 endpoint에 존재한다는 사실만으로 최초 상장일을 추정하지 않는다. 따라서
    `listed_at_utc`/`delisted_at_utc`는 비워 두고, 관측 거래일의 active snapshot으로
    `is_active=True`만 기록한다.
    """
    sources = (
        (AssetClass.LEVERAGED_ETF, client.fetch_daily_etf_trades(trading_date)),
        (AssetClass.LEVERAGED_ETN, client.fetch_daily_etn_trades(trading_date)),
    )
    products: list[DiscoveredLeveragedProduct] = []
    exclusions: list[LeveragedProductExclusion] = []
    seen_symbols: set[str] = set()

    for asset_class, rows in sources:
        for row in rows:
            display_name = _optional_text(row.get("ISU_NM"))
            if display_name is None or _SINGLE_STOCK_MARKER not in display_name:
                continue
            source_symbol = _optional_text(row.get("ISU_CD"))
            try:
                product = _classify_product(row, asset_class, trading_date)
                if target_underlyings is not None and product.underlying_name not in target_underlyings:
                    continue
                if product.source_symbol in seen_symbols:
                    raise ValueError("ETF/ETN 응답에서 종목코드가 중복됨")
            except (InvalidOperation, TypeError, ValueError) as exc:
                exclusions.append(
                    LeveragedProductExclusion(
                        asset_class=asset_class,
                        source_symbol=source_symbol,
                        display_name=display_name,
                        reason=str(exc),
                    )
                )
                continue

            seen_symbols.add(product.source_symbol)
            master.register_instrument(
                InstrumentRecord(
                    instrument_id=product.instrument_id,
                    asset_class=product.asset_class,
                    primary_venue=Venue.KRX,
                    display_name=product.display_name,
                    is_active=True,
                )
            )
            products.append(product)

    products.sort(key=lambda item: (item.asset_class.value, item.source_symbol))
    return LeverageUniverseDiscoveryResult(trading_date, tuple(products), tuple(exclusions))


def _classify_product(
    row: dict[str, Any], asset_class: AssetClass, trading_date: date
) -> DiscoveredLeveragedProduct:
    symbol = _required_text(row, "ISU_CD")
    display_name = _required_text(row, "ISU_NM")
    reference_index_name = _required_text(row, "IDX_IND_NM")
    basis_date = _parse_basis_date(_required_text(row, "BAS_DD"))
    if basis_date != trading_date:
        raise ValueError(
            f"요청 거래일과 BAS_DD가 다름: requested={trading_date}, response={basis_date}"
        )
    leverage_factor = _parse_leverage_factor(display_name)
    underlying_name = _parse_underlying_name(reference_index_name)
    nav_field = "NAV" if asset_class is AssetClass.LEVERAGED_ETF else "PER1SECU_INDIC_VAL"
    return DiscoveredLeveragedProduct(
        instrument_id=f"KRX_{symbol}_{asset_class.value}",
        source_symbol=symbol,
        display_name=display_name,
        asset_class=asset_class,
        underlying_name=underlying_name,
        leverage_factor=leverage_factor,
        basis_date=basis_date,
        reference_index_name=reference_index_name,
        nav_or_indicative_value=_optional_decimal(row.get(nav_field)),
        listed_shares=_optional_decimal(row.get("LIST_SHRS")),
    )


def _parse_leverage_factor(display_name: str) -> Decimal:
    compact = re.sub(r"\s+", "", display_name).upper()
    multiple_match = re.search(r"(?P<multiple>\d+(?:\.\d+)?)X", compact)
    if "인버스" in compact:
        if multiple_match is None:
            return Decimal("-1")
        return -Decimal(multiple_match.group("multiple"))
    if "레버리지" in compact:
        if multiple_match is None:
            return Decimal("2")
        return Decimal(multiple_match.group("multiple"))
    raise ValueError("단일종목 상품명에 레버리지·인버스 배율 marker가 없음")


def _parse_underlying_name(reference_index_name: str) -> str:
    tokens = [
        token
        for token in reference_index_name.replace("·", " ").split()
        if token.upper() not in _INDEX_NOISE_TOKENS
    ]
    if not tokens:
        raise ValueError("기초지수명에서 단일 기초자산을 추출할 수 없음")
    return " ".join(tokens)


def _parse_basis_date(value: str) -> date:
    if len(value) != 8 or not value.isdigit():
        raise ValueError("BAS_DD는 YYYYMMDD 형식이어야 함")
    return date(int(value[:4]), int(value[4:6]), int(value[6:]))


def _required_text(row: dict[str, Any], field_name: str) -> str:
    value = _optional_text(row.get(field_name))
    if value is None:
        raise ValueError(f"필수 필드 누락: {field_name}")
    return value


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if not isinstance(value, (str, int)):
        raise TypeError("숫자 필드는 문자열 또는 정수여야 함")
    return Decimal(str(value).replace(",", ""))
