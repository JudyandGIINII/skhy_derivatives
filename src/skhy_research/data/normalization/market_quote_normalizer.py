"""raw payload를 도메인 타입으로 매핑하는 anti-corruption 계층 (FR-04).

실제 KRX/KIS/Toss 응답 스키마는 G-02 capability probe로 확정되므로, 여기서는
canonical 필드명을 이미 사용하는 우리 자신의 fixture payload(P0-07)를 기준으로
매핑 계약을 정의한다. 실제 공급자별 필드 변환(리네이밍·단위 변환)은
`adapters/providers/<provider>/normalizer.py`가 이 계약을 구현하며 Phase 1에서
실제 payload 구조 확인 후 채운다. 결측·오염 데이터를 0이나 기본값으로
치환하지 않고 `NormalizationError`로 명시 실패시킨다.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from skhy_research.data.normalization.errors import NormalizationError
from skhy_research.domain.market import Bar, MarketQuote, Trade


def normalize_market_quote(
    source: str, dataset: str, raw: dict[str, Any], raw_record_id: str | None = None
) -> MarketQuote:
    try:
        return MarketQuote(**raw)
    except ValidationError as exc:
        raise NormalizationError(source, dataset, str(exc), raw_record_id) from exc


def normalize_trade(
    source: str, dataset: str, raw: dict[str, Any], raw_record_id: str | None = None
) -> Trade:
    try:
        return Trade(**raw)
    except ValidationError as exc:
        raise NormalizationError(source, dataset, str(exc), raw_record_id) from exc


def normalize_bar(
    source: str, dataset: str, raw: dict[str, Any], raw_record_id: str | None = None
) -> Bar:
    try:
        return Bar(**raw)
    except ValidationError as exc:
        raise NormalizationError(source, dataset, str(exc), raw_record_id) from exc
