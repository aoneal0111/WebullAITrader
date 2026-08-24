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
    classification: str = "--"
    float_shares: str = "--"
    setup: str = "--"
    setup_state: str = "--"
    distance_to_hod: str = "--"
    strategy_status: str = "--"
    explanations: str = "--"
    float_provenance: str = "--"
    entry_trigger: str = "--"
    stop_price: str = "--"
    blocking_reasons: str = "--"


@dataclass(frozen=True, slots=True)
class WatchlistSnapshot:
    rows: tuple[WatchlistRow, ...] = ()
    sort_field: str = "projection"
    descending: bool = False
    empty_title: str = "Atlas is scanning"
    empty_detail: str = (
        "High-confidence opportunities\n"
        "will appear here automatically."
    )
    scanner_status: str = "Unknown"
    candidate_count: int = 0
