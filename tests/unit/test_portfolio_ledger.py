"""P1-05 검증: 포트폴리오 원장의 현금·평단·실현손익 계산."""

from __future__ import annotations

from decimal import Decimal

from skhy_research.engine.portfolio import PortfolioLedger


def test_single_buy_updates_cash_position_and_avg_cost() -> None:
    ledger = PortfolioLedger()
    ledger.apply_fill("000660", "BUY", Decimal("10"), Decimal("200000"), "KRW")

    assert ledger.positions["000660"] == Decimal("10")
    assert ledger.avg_cost["000660"] == Decimal("200000")
    assert ledger.cash_by_currency["KRW"] == Decimal("-2000000")


def test_buy_then_sell_realizes_pnl() -> None:
    ledger = PortfolioLedger()
    ledger.apply_fill("000660", "BUY", Decimal("10"), Decimal("200000"), "KRW")
    ledger.apply_fill("000660", "SELL", Decimal("10"), Decimal("205000"), "KRW")

    assert ledger.positions["000660"] == Decimal("0")
    assert ledger.realized_pnl == Decimal("50000")  # (205000-200000)*10
    assert ledger.cash_by_currency["KRW"] == Decimal("50000")  # -2,000,000 + 2,050,000


def test_partial_sell_realizes_proportional_pnl_and_keeps_avg_cost() -> None:
    ledger = PortfolioLedger()
    ledger.apply_fill("000660", "BUY", Decimal("10"), Decimal("200000"), "KRW")
    ledger.apply_fill("000660", "SELL", Decimal("4"), Decimal("210000"), "KRW")

    assert ledger.positions["000660"] == Decimal("6")
    assert ledger.realized_pnl == Decimal("40000")  # (210000-200000)*4
    assert ledger.avg_cost["000660"] == Decimal("200000")  # 잔여 포지션 평단 유지


def test_multiple_buys_average_cost_correctly() -> None:
    ledger = PortfolioLedger()
    ledger.apply_fill("000660", "BUY", Decimal("10"), Decimal("200000"), "KRW")
    ledger.apply_fill("000660", "BUY", Decimal("10"), Decimal("220000"), "KRW")

    assert ledger.positions["000660"] == Decimal("20")
    assert ledger.avg_cost["000660"] == Decimal("210000")  # (200000*10+220000*10)/20


def test_unrealized_pnl_uses_mark_price() -> None:
    ledger = PortfolioLedger()
    ledger.apply_fill("000660", "BUY", Decimal("10"), Decimal("200000"), "KRW")

    unrealized = ledger.unrealized_pnl({"000660": Decimal("210000")})
    assert unrealized == Decimal("100000")  # (210000-200000)*10


def test_unrealized_pnl_ignores_instruments_without_mark_price() -> None:
    ledger = PortfolioLedger()
    ledger.apply_fill("000660", "BUY", Decimal("10"), Decimal("200000"), "KRW")

    assert ledger.unrealized_pnl({}) == Decimal("0")


def test_short_position_pnl_direction() -> None:
    ledger = PortfolioLedger()
    ledger.apply_fill("000660", "SELL", Decimal("10"), Decimal("200000"), "KRW")  # 숏 진입
    ledger.apply_fill("000660", "BUY", Decimal("10"), Decimal("190000"), "KRW")  # 환매수(청산)

    assert ledger.positions["000660"] == Decimal("0")
    assert ledger.realized_pnl == Decimal("100000")  # 숏에서 가격 하락 -> 이익: (200000-190000)*10
