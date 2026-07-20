from __future__ import annotations

from decimal import Decimal

from app.analytics.models import ExposureAnalytics
from app.paper_trading.models import EquityPoint, PaperPortfolio

ZERO = Decimal(0)
HUNDRED = Decimal(100)


def analyze_exposure(
    history: tuple[PaperPortfolio, ...] | None, equity_curve: tuple[EquityPoint, ...]
) -> ExposureAnalytics:
    prerequisite = "Authoritative portfolio history aligned with the equity curve is required."
    holding = "Authoritative entry allocation is required for holding-duration analytics."
    if history is None or len(history) != len(equity_curve) or not history:
        return _unavailable((prerequisite, holding))
    if any(
        portfolio.timestamp != point.timestamp or portfolio.equity != point.equity
        for portfolio, point in zip(history, equity_curve, strict=True)
    ):
        raise ValueError("portfolio history is not aligned with the equity curve")
    if any(portfolio.equity <= ZERO for portfolio in history):
        raise ValueError("portfolio equity must be positive for exposure analytics")
    intervals = []
    for portfolio, next_portfolio in zip(history[:-1], history[1:], strict=True):
        duration = _microseconds(next_portfolio.timestamp - portfolio.timestamp)
        if duration <= 0:
            raise ValueError("portfolio history timestamps must be strictly increasing")
        gross_value = sum((abs(item.market_value) for item in portfolio.positions), ZERO)
        net_value = sum((item.market_value for item in portfolio.positions), ZERO)
        gross = gross_value / portfolio.equity * HUNDRED
        net = net_value / portfolio.equity * HUNDRED
        intervals.append((duration, gross, net, gross))
    if not intervals:
        return ExposureAnalytics(True, 0, 0, None, None, None, None, None, None, None,
                                 None, None, None, None, (holding,))
    observed = sum(item[0] for item in intervals)
    invested = sum(item[0] for item in intervals if item[1] > ZERO)
    weighted = lambda index: sum((Decimal(item[0]) * item[index] for item in intervals), ZERO) / Decimal(observed)
    return ExposureAnalytics(
        True, observed, invested, Decimal(invested) / Decimal(observed) * HUNDRED,
        weighted(1), max(item[1] for item in intervals), weighted(2),
        max(abs(item[2]) for item in intervals), weighted(3), max(item[3] for item in intervals),
        None, None, None, None, (holding,),
    )


def _unavailable(prerequisites: tuple[str, ...]) -> ExposureAnalytics:
    return ExposureAnalytics(False, None, None, None, None, None, None, None, None, None,
                             None, None, None, None, prerequisites)


def _microseconds(delta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
