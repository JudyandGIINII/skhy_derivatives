"""KRX 일별 ETF/ETN proxy 입력의 정규화 계약."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from skhy_research.domain.enums import AssetClass


class KrxEtpDailySnapshot(BaseModel):
    """단일종목 레버리지 ETP의 기준일 NAV/IV·상장좌수 snapshot."""

    model_config = ConfigDict(frozen=True)

    fund_id: str = Field(min_length=1)
    source_symbol: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    asset_class: AssetClass
    underlying_name: str = Field(min_length=1)
    leverage_factor: Decimal
    basis_date: date
    nav_or_indicative_value: Decimal = Field(gt=0)
    listed_shares: Decimal = Field(gt=0)
    raw_record_id: str = Field(min_length=1)
