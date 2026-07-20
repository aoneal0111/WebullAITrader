from __future__ import annotations

from app.backtesting.results import BacktestResult


def render_text_report(result: BacktestResult) -> str:
    return "\n".join((
        "HISTORICAL PAPER SIMULATION ONLY",
        f"Period: {result.start_timestamp.isoformat()} to {result.end_timestamp.isoformat()}",
        f"Candles: {result.number_of_candles}",
        f"Proposals: {result.number_of_proposals} (approved {result.number_approved}, rejected {result.number_rejected})",
        f"Fills: {result.number_filled}",
        f"Ending cash: {result.ending_cash}",
        f"Ending equity: {result.ending_equity}",
        f"Total return: {result.total_return}%",
        f"Maximum drawdown: {result.maximum_drawdown}%",
    ))
