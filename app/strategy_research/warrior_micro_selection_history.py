"""Read-only normalized SIP symbol-day adapter for Warrior selection timing.

Never requests market data, changes configurations, or submits broker orders.
Use a previously verified <SYMBOL>_<YYYY-MM-DD>.jsonl normalized bar partition.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import re

from app.strategies.warrior_momentum.models import MinuteBar
from .episode_price_study import BarCache
from .warrior_micro_selection_timing import review_micro_selection


def run(source: Path, *, symbol: str, day: str, output: Path) -> dict:
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", symbol):
        raise ValueError("INVALID_SYMBOL")
    datetime.strptime(day, "%Y-%m-%d")
    source = Path(source)
    if source.is_symlink():
        raise ValueError("EXPLICIT_NORMALIZED_PARTITION_REQUIRED")
    source = source.resolve(strict=True)
    output = Path(output)
    if output.exists():
        raise ValueError("OUTPUT_ALREADY_EXISTS")
    expected = f"{symbol}_{day}.jsonl"
    if source.name != expected or source.is_symlink() or not source.is_file():
        raise ValueError("EXPLICIT_NORMALIZED_PARTITION_REQUIRED")
    cache = BarCache(source.parent)
    bars = cache.get(symbol, day)
    # Keep the detector's bounded study contract explicit rather than
    # truncating an entire session or silently skipping early candles.
    if len(bars) > 600:
        raise ValueError("STUDY_BAR_LIMIT_EXCEEDED_SPLIT_WINDOW_EXPLICITLY")
    converted = tuple(
        MinuteBar(b.symbol, b.timestamp, b.open, b.high, b.low, b.close, b.volume)
        for b in bars
    )
    observations = review_micro_selection(converted)
    cache.verify_stable()
    digest = cache.proofs[(symbol, day)][2]
    report = dict(
        scope="WARRIOR_MICRO_SELECTION_OFFLINE_V1",
        symbol=symbol, day=day, source_sha256=digest,
        bars=len(converted), observations=[{
            **asdict(item),
            "at": item.at.isoformat(),
            "micro_trigger": str(item.micro_trigger),
            "selected_trigger": None if item.selected_trigger is None else str(item.selected_trigger),
            "micro_close": str(item.micro_close),
            "trigger_premium": None if item.trigger_premium is None else str(item.trigger_premium),
        } for item in observations],
        orders=0, network_requests=0, parameters_changed=False,
        execution_authorized=False, promotion="NONE",
        limitations=[
            "Retrospective structural trigger comparison, not measured order latency or slippage",
            "Only completed minute bars; no intrabar or executable BID/ASK evidence",
            "No trading admission, share sizing, profit/loss or win-rate estimate",
            "Explicit single normalized symbol-day partition, at most 600 bars",
        ],
    )
    # Create-only, never overwrite historical evidence or existing reports.
    with output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--day", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = run(args.source, symbol=args.symbol, day=args.day, output=args.output)
    print(f"ROWS={len(result['observations'])}")
    print(f"REPORT={args.output}")


if __name__ == "__main__":
    main()
