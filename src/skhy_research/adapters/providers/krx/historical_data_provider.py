"""KRX 일별매매정보를 공통 ``Bar`` 계약으로 변환하는 실 어댑터."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from datetime import time as wall_time
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

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
from skhy_research.ports.errors import ProviderRateLimitError, ProviderResponseError

_DEFAULT_INSTRUMENT_SYMBOLS = {
    "SKHY_000660_KRX_COMMON": "000660",
    "KRX_000660_COMMON_STOCK": "000660",
    "KRX_005930_COMMON_STOCK": "005930",
    "000660": "000660",
    "005930": "005930",
}
_MAX_RANGE_DAYS = 366


class _KrxDailyStockClient(Protocol):
    def capabilities(self) -> ProviderCatalogEntry: ...

    def fetch_daily_stock_trades(self, trading_date: date) -> list[dict[str, Any]]: ...


# observer는 이미 저장된 동일 raw가 있으면 그 canonical 수신시각을 반환할 수 있다.
DailyRecordsObserver = Callable[[date, list[dict[str, Any]], int], int | None]


@dataclass(frozen=True)
class KrxTradingDayPrefetch:
    trading_dates: tuple[date, ...]
    non_trading_weekdays: tuple[date, ...]
    fetched_dates: tuple[date, ...]


@dataclass(frozen=True)
class _CachedDailyRecords:
    records: list[dict[str, Any]]
    received_time_utc: int


class InsufficientKrxTradingDaysError(RuntimeError):
    """허용한 lookback 안에서 요청한 수의 KRX 거래일 응답을 확보하지 못함."""


class KrxHistoricalDataProvider:
    """KRX 조회용 원문을 ``HistoricalDataProvider`` 포트에 연결한다."""

    def __init__(
        self,
        client: _KrxDailyStockClient,
        instrument_symbols: dict[str, str] | None = None,
        *,
        min_request_interval_seconds: float = 0.2,
        max_rate_limit_retries: int = 4,
        records_observer: DailyRecordsObserver | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if min_request_interval_seconds < 0:
            raise ValueError("min_request_interval_seconds는 음수일 수 없다")
        if max_rate_limit_retries < 0:
            raise ValueError("max_rate_limit_retries는 음수일 수 없다")
        self._client = client
        self._instrument_symbols = dict(instrument_symbols or _DEFAULT_INSTRUMENT_SYMBOLS)
        self._min_request_interval_seconds = min_request_interval_seconds
        self._max_rate_limit_retries = max_rate_limit_retries
        self._records_observer = records_observer
        self._monotonic = monotonic
        self._sleep = sleep
        self._clock_ns = clock_ns
        self._last_request_at: float | None = None
        self._daily_cache: dict[date, _CachedDailyRecords] = {}

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

        bars: list[Bar] = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                cached = self._get_daily_records(current)
                matching = next(
                    (row for row in cached.records if row.get("ISU_CD") == symbol), None
                )
                if matching is not None:
                    bars.append(
                        _record_to_bar(
                            matching,
                            instrument_id=instrument_id,
                            symbol=symbol,
                            received_time_utc=cached.received_time_utc,
                        )
                    )
            current += timedelta(days=1)
        return bars

    def prefetch_latest_trading_days(
        self,
        *,
        end: date,
        minimum_trading_days: int,
        max_lookback_calendar_days: int = _MAX_RANGE_DAYS,
    ) -> KrxTradingDayPrefetch:
        """날짜별 전종목 응답을 한 번씩 받아 최근 KRX 거래일을 확정·cache한다."""

        if minimum_trading_days <= 0:
            raise ValueError("minimum_trading_days는 양수여야 한다")
        if not 1 <= max_lookback_calendar_days <= _MAX_RANGE_DAYS:
            raise ValueError(f"max_lookback_calendar_days는 1~{_MAX_RANGE_DAYS}여야 한다")

        trading_dates: list[date] = []
        non_trading_weekdays: list[date] = []
        fetched_dates: list[date] = []
        current = end
        lower_bound = end - timedelta(days=max_lookback_calendar_days - 1)
        while current >= lower_bound and len(trading_dates) < minimum_trading_days:
            if current.weekday() < 5:
                cached = self._get_daily_records(current)
                fetched_dates.append(current)
                if cached.records:
                    trading_dates.append(current)
                else:
                    non_trading_weekdays.append(current)
            current -= timedelta(days=1)

        if len(trading_dates) < minimum_trading_days:
            raise InsufficientKrxTradingDaysError(
                "KRX 거래일 부족: "
                f"requested={minimum_trading_days}, collected={len(trading_dates)}, "
                f"lookback_calendar_days={max_lookback_calendar_days}"
            )
        return KrxTradingDayPrefetch(
            trading_dates=tuple(sorted(trading_dates)),
            non_trading_weekdays=tuple(sorted(non_trading_weekdays)),
            fetched_dates=tuple(sorted(fetched_dates)),
        )

    def _get_daily_records(self, trading_date: date) -> _CachedDailyRecords:
        cached = self._daily_cache.get(trading_date)
        if cached is not None:
            return cached

        for attempt in range(self._max_rate_limit_retries + 1):
            self._wait_for_request_slot()
            try:
                records = self._client.fetch_daily_stock_trades(trading_date)
            except ProviderRateLimitError as exc:
                self._last_request_at = self._monotonic()
                if attempt >= self._max_rate_limit_retries:
                    raise
                exponential_backoff = self._min_request_interval_seconds * (2**attempt)
                self._sleep(max(exc.retry_after_seconds, exponential_backoff))
                continue

            self._last_request_at = self._monotonic()
            received_time_utc = self._clock_ns()
            if self._records_observer is not None:
                canonical_received_time = self._records_observer(
                    trading_date, records, received_time_utc
                )
                if canonical_received_time is not None:
                    received_time_utc = canonical_received_time
            cached = _CachedDailyRecords(records, received_time_utc)
            self._daily_cache[trading_date] = cached
            return cached

        raise AssertionError("KRX retry loop exhausted without returning or raising")

    def _wait_for_request_slot(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._monotonic() - self._last_request_at
        remaining = self._min_request_interval_seconds - elapsed
        if remaining > 0:
            self._sleep(remaining)


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
