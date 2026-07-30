from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChartViewSnapshot:
    symbol: str = "--"
    timeframe: str = "1D"
    market_status: str = "UNKNOWN"
    message: str = "Select a symbol to initialize the market chart."


__all__ = ["ChartViewSnapshot"]
