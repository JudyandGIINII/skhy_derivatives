"""KRX 일별매매정보를 공통 ``Bar`` 계약으로 변환하는 실 어댑터."""

from __future__ import annotations

import time
from datetime import date, timedelta
from datetime import time as wall_time
from decimal import Decimal, InvalidOperation
from typing import Any

from skhy_research.adapters.providers.krx.client import KrxReadOnlyClient
from skhy_research.domain.calendar import (
    local_datetime_to_utc_nanos,
    utc_nanos_to_local_datetime,
)
from skhy_research.domain.enums import (
    AdjustmentStatus,
    Currency,
    QualityFlag,
    Session,
    Venue,
)
from skhy_research.domain.market import Bar, BarConstructionMethod
from skhy_research.domain.provider_capability import ProviderCatalogEntry
from skhy_research.ports.errors import ProviderResponseError

_DEFAULT_INSTRUMENT_SYMBOLS = {
    "SKHY_000660_KRX_COMMON": "000660",
    "000660": "000660",
}
_MAX_RANGE_DAYS = 366


class KrxHistoricalDataProvider:
    """KRX 조회용 원문을 ``HistoricalDataProvider`` 포트에 연결한다."""

    def __init__(
        self,
        client: KrxReadOnlyClient,
        instrument_symbols: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        self._instrument_symbols = dict(instrument_symbols or _DEFAULT_INSTRUMENT_SYMBOLS)

    def capabilities(self) -> ProviderCatalogEntry:
        return self._client.capabilities()

    def get_bars(
        self,
        instrument_id: str,
        period: str,
        start_utc: int,
        end_utc: int,
        adjustment: AdjustmentStatus,
    ) -> list[Bar]:
        if period != "1d":
            raise ValueError("KRX Open API 어댑터는 period='1d'만 지원한다")
        if adjustment is not AdjustmentStatus.RAW:
            raise ValueError("KRX Open API 원문은 adjustment=RAW로만 반환한다")
        try:
            symbol = self._instrument_symbols[instrument_id]
        except KeyError as exc:
            raise ValueError(f"등록되지 않은 instrument_id: {instrument_id}") from exc

        start = utc_nanos_to_local_datetime(start_utc, Venue.KRX).date()
        end = utc_nanos_to_local_datetime(end_utc, Venue.KRX).date()
        if end < start:
            raise ValueError("end_utc는 start_utc보다 이를 수 없다")
        if (end - start).days > _MAX_RANGE_DAYS:
            raise ValueError(f"단일 호출 범위는 {_MAX_RANGE_DAYS}일을 초과할 수 없다")

        received_time_utc = time.time_ns()
        bars: list[Bar] = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                records = self._client.fetch_daily_stock_trades(current)
                matching = next((row for row in records if row.get("ISU_CD") == symbol), None)
                if matching is not None:
                    bars.append(
                        _record_to_bar(
                            matching,
                            instrument_id=instrument_id,
                            symbol=symbol,
                            received_time_utc=received_time_utc,
                        )
                    )
            current += timedelta(days=1)
        return bars


def _record_to_bar(
    record: dict[str, Any],
    *,
    instrument_id: str,
    symbol: str,
    received_time_utc: int,
) -> Bar:
    try:
        trading_date = _parse_date(record["BAS_DD"])
        bar_close_time = local_datetime_to_utc_nanos(
            trading_date,
            wall_time(15, 30),
            Venue.KRX,
        )
        return Bar(
            source="KRX_OPEN_API",
            venue=Venue.KRX,
            symbol=symbol,
            event_time_utc=bar_close_time,
            received_time_utc=max(received_time_utc, bar_close_time),
            currency=Currency.KRW,
            session=Session.REGULAR,
            is_delayed=True,
            adjustment_status=AdjustmentStatus.RAW,
            quality_flag=[QualityFlag.DELAYED],
            instrument_id=instrument_id,
            period="1d",
            open=_decimal(record["TDD_OPNPRC"]),
            high=_decimal(record["TDD_HGPRC"]),
            low=_decimal(record["TDD_LWPRC"]),
            close=_decimal(record["TDD_CLSPRC"]),
            volume=_decimal(record["ACC_TRDVOL"]),
            turnover=_decimal(record["ACC_TRDVAL"]),
            is_adjusted=False,
            construction=BarConstructionMethod(
                method="VENDOR_PROVIDED",
                source_segment=f"KRX_OPEN_API:{trading_date.isoformat()}",
            ),
            bar_close_time_utc=bar_close_time,
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ProviderResponseError("krx", error_code="invalid-daily-bar-schema") from exc


def _decimal(value: object) -> Decimal:
    if not isinstance(value, (str, int)):
        raise TypeError("numeric field must be a string or integer")
    return Decimal(str(value).replace(",", ""))


def _parse_date(value: object) -> date:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ValueError("BAS_DD must be YYYYMMDD")
    return date(int(value[:4]), int(value[4:6]), int(value[6:]))
