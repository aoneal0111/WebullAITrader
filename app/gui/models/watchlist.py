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
    rank: str = "--"
    score: str = "--"
    relative_volume: str = "--"
    dollar_volume: str = "--"
    spread: str = "--"
    catalyst: str = "--"
    passed_rules: str = "--"
    failed_rules: str = "--"
    freshness: str = "--"
    session: str = "--"


@dataclass(frozen=True, slots=True)
class WatchlistSnapshot:
    rows: tuple[WatchlistRow, ...] = ()
    sort_field: str = "projection"
    descending: bool = False
