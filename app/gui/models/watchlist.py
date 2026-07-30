from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WatchlistRow:
    symbol: str
    selected: bool
    latest_price: str
    change: str
    change_percent: str
    bid: str
    ask: str
    volume: str
    market_status: str
    last_update: str
    stale: str


@dataclass(frozen=True, slots=True)
class WatchlistSnapshot:
    rows: tuple[WatchlistRow, ...] = ()
