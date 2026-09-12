"""High-recall daily candidate discovery; not an Atlas trading rule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable
import json


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


def candidate_to_record(item: CandidateDay) -> dict[str, object]:
    return {"symbol": item.symbol, "trading_date": item.trading_date.isoformat(), "reasons": item.reasons,
            "open": str(item.open), "high": str(item.high), "low": str(item.low), "close": str(item.close),
            "volume": str(item.volume), "previous_close": None if item.previous_close is None else str(item.previous_close),
            "gap_percent": None if item.gap_percent is None else str(item.gap_percent),
            "change_percent": str(item.change_percent), "range_percent": str(item.range_percent),
            "dollar_volume": str(item.dollar_volume)}


def candidate_from_record(row: dict[str, object]) -> CandidateDay:
    return CandidateDay(str(row["symbol"]).upper(), date.fromisoformat(str(row["trading_date"])),
                        tuple(str(reason) for reason in row["reasons"]), Decimal(str(row["open"])),
                        Decimal(str(row["high"])), Decimal(str(row["low"])), Decimal(str(row["close"])),
                        Decimal(str(row["volume"])), None if row.get("previous_close") is None else Decimal(str(row["previous_close"])),
                        None if row.get("gap_percent") is None else Decimal(str(row["gap_percent"])),
                        Decimal(str(row["change_percent"])), Decimal(str(row["range_percent"])),
                        Decimal(str(row["dollar_volume"])))


def candidate_records_hash(items: Iterable[CandidateDay]) -> str:
    import hashlib
    payload = "\n".join(json.dumps(candidate_to_record(item), sort_keys=True, separators=(",", ":"), default=str)
                     for item in items).encode()
    return hashlib.sha256(payload).hexdigest()


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
