"""High-recall daily candidate discovery; not an Atlas trading rule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CandidateDay:
    symbol: str
    trading_date: date
    reasons: tuple[str, ...]
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    previous_close: Decimal | None
    gap_percent: Decimal | None
    change_percent: Decimal
    range_percent: Decimal
    dollar_volume: Decimal


def discover_candidate_days(rows: list[dict[str, object]], *, minimum_move_percent: Decimal = Decimal("5"),
                            minimum_range_percent: Decimal = Decimal("8")) -> tuple[CandidateDay, ...]:
    result = []
    for row in rows:
        close, opened = Decimal(str(row["close"])), Decimal(str(row["open"]))
        high, low, volume = Decimal(str(row["high"])), Decimal(str(row["low"])), Decimal(str(row["volume"]))
        prior = None if row.get("previous_close") is None else Decimal(str(row["previous_close"]))
        change = (close - opened) / opened * 100
        range_percent = (high - low) / opened * 100
        gap = None if prior in (None, 0) else (opened - prior) / prior * 100
        reasons = tuple(reason for reason, passed in (("DAILY_MOVE", abs(change) >= minimum_move_percent),
                         ("DAILY_RANGE", range_percent >= minimum_range_percent), ("GAP", gap is not None and abs(gap) >= minimum_move_percent)) if passed)
        if not reasons: continue
        result.append(CandidateDay(str(row["symbol"]).upper(), date.fromisoformat(str(row["trading_date"])), reasons,
                                   opened, high, low, close, volume, prior, gap, change, range_percent, close * volume))
    return tuple(sorted(result, key=lambda item: (item.trading_date, item.symbol)))


def attach_previous_closes(rows: Iterable[dict[str, object]], *, start_date: date) -> tuple[dict[str, object], ...]:
    """Attach the latest earlier daily close; rows must be regular-session daily data."""
    ordered = sorted((dict(row) for row in rows), key=lambda row: (str(row.get("symbol", "")).upper(), str(row.get("trading_date", ""))))
    last_close: dict[str, Decimal] = {}
    result: list[dict[str, object]] = []
    for row in ordered:
        symbol = str(row.get("symbol", "")).upper()
        day = date.fromisoformat(str(row["trading_date"]))
        if day >= start_date:
            row["previous_close"] = None if symbol not in last_close else str(last_close[symbol])
            result.append(row)
        last_close[symbol] = Decimal(str(row["close"]))
    return tuple(result)
