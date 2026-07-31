from __future__ import annotations

from decimal import Decimal

from app.gui.models.portfolio import PortfolioDashboardSnapshot
from app.read_models.portfolio import PortfolioHighlight, PortfolioSummary


def format_portfolio(
    summary: PortfolioSummary,
    *,
    equity: Decimal | None = None,
    buying_power: Decimal | None = None,
    cash: Decimal | None = None,
) -> PortfolioDashboardSnapshot:
    if not isinstance(summary, PortfolioSummary):
        raise TypeError("summary must be a PortfolioSummary")
    return PortfolioDashboardSnapshot(
        metrics=(
            ("Equity", _decimal_money(equity)),
            ("Buying Power", _decimal_money(buying_power)),
            ("Cash", _decimal_money(cash)),
            ("Market Value", _money(summary.total_market_value)),
            ("Cost Basis", _money(summary.total_cost_basis)),
            ("Total P/L", _money(summary.total_pnl, signed=True)),
            ("Realized P/L", _money(summary.realized_pnl, signed=True)),
            ("Unrealized P/L", _money(summary.unrealized_pnl, signed=True)),
            ("Gross Exposure", _money(summary.gross_exposure)),
            ("Exposure", _exposure_percent(summary.gross_exposure, equity)),
            ("Long / Short", _exposure(summary)),
            (
                "Working Orders",
                str(summary.working_orders),
            ),
            ("Open Positions", str(summary.open_positions)),
            (
                "Positions / Orders",
                f"{summary.open_positions} / {summary.working_orders}",
            ),
            (
                "Winners / Losers",
                _counts(
                    summary.winning_positions,
                    summary.losing_positions,
                ),
            ),
        ),
        highlights=(
            ("Largest Position", _highlight(summary.largest_position)),
            (
                "Largest Gain",
                _highlight(summary.largest_unrealized_gain, signed=True),
            ),
            (
                "Largest Loss",
                _highlight(summary.largest_unrealized_loss, signed=True),
            ),
        ),
    )


def _exposure(summary: PortfolioSummary) -> str:
    if summary.long_exposure is None or summary.short_exposure is None:
        return "--"
    return (
        f"{_money(summary.long_exposure)} / "
        f"{_money(summary.short_exposure)}"
    )


def _exposure_percent(value: str | None, equity: Decimal | None) -> str:
    if value is None or equity is None or equity == 0:
        return "--"
    return f"{(Decimal(value) / equity * Decimal('100')):.1f}%"


def _counts(first: int | None, second: int | None) -> str:
    if first is None or second is None:
        return "--"
    return f"{first} / {second}"


def _highlight(
    value: PortfolioHighlight | None,
    *,
    signed: bool = False,
) -> str:
    if value is None:
        return "--"
    return f"{value.symbol} {_money(value.value, signed=signed)}"


def _money(value: str | None, *, signed: bool = False) -> str:
    if value is None:
        return "--"
    amount = Decimal(value)
    sign = "+" if signed and amount > 0 else ""
    return f"{sign}${amount:,.2f}"


def _decimal_money(value: Decimal | None) -> str:
    if value is None:
        return "--"
    return f"${value:,.2f}"
