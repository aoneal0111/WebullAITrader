"""Offline, bounded aggregation of completed-bar Warrior timing reports.

No market data acquisition, broker access, parameter changes, or inferred fills.
The time-deduplicated counts are *episodes of observed triggers*, not trades.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path


def aggregate(directory: Path) -> dict:
    directory = Path(directory)
    paths = sorted(directory.glob("*-timing.json"))
    if not 1 <= len(paths) <= 600:
        raise ValueError("REPORT_COUNT_BOUND")
    counts = Counter()
    selected = Counter()
    price_comparison = Counter()
    distinct = []
    seen = set()
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
            raise ValueError("REPORT_FILE_INVALID")
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("scope") != "WARRIOR_MICRO_SELECTION_OFFLINE_V1":
            raise ValueError("REPORT_SCOPE_INVALID")
        symbol, day = report["symbol"], report["day"]
        if path.name != f"{symbol}_{day}-timing.json" or (symbol, day) in seen:
            raise ValueError("REPORT_IDENTITY_MISMATCH")
        seen.add((symbol, day))
        rows = report["observations"]
        if not isinstance(rows, list) or len(rows) > 1440:
            raise ValueError("OBSERVATION_BOUND")
        bars = report["bars"]
        if type(bars) is not int or not 0 <= bars <= 1440:
            raise ValueError("BAR_COUNT_INVALID")
        states = report.get("micro_states", {})
        if states and (sum(states.values()) != bars or states.get('TRIGGERED', 0) != len(rows)):
            raise ValueError("STATE_TOTAL_MISMATCH")
        counts["sessions"] += 1
        counts["bars"] += bars
        counts["gap_boundaries"] += report.get("gap_boundaries", 0)
        counts["forming_assessments"] += states.get("FORMING", 0)
        counts["trigger_assessments"] += len(rows)
        if rows:
            counts["sessions_with_trigger"] += 1
        prior = None
        for row in rows:
            at = datetime.fromisoformat(row["at"])
            # UTC completion date may differ from exchange date.
            if at.utcoffset() is None:
                raise ValueError("AWARE_TIME_REQUIRED")
            if prior is not None and at <= prior:
                raise ValueError("TRIGGER_ORDER_INVALID")
            # Adjacent completed minute evaluations are one continuous run;
            # a gap starts another *observed* run, not a fresh trade.
            if prior is None or at - prior > timedelta(minutes=1):
                counts["trigger_runs"] += 1
                distinct.append({"symbol": symbol, "day": day, "first_trigger_at": at.isoformat()})
            prior = at
            setup = row.get("selected_type") or "NONE"
            selected[setup] += 1
            premium = row.get("trigger_premium")
            if premium is None:
                price_comparison["unavailable"] += 1
            else:
                difference = Decimal(str(premium))
                if not difference.is_finite():
                    raise ValueError("NONFINITE_PREMIUM")
                price_comparison["higher" if difference > 0 else "lower" if difference < 0 else "same"] += 1
    return dict(
        scope="WARRIOR_TIMING_REPORT_AGGREGATE_V1",
        metrics=dict(counts), selected_setup_assessments=dict(selected),
        trigger_price_comparison=dict(price_comparison),
        observed_trigger_runs=distinct,
        orders=0, network_requests=0, execution_authorized=False,
        limitations=[
            "Observed consecutive trigger runs are not independently verified structural episodes or fills",
            "Chosen historical symbol-days are retrospective and not a fresh holdout",
            "Trigger premium is not a fill price, entry delay, or net P&L",
            "Scanner discovery, quote freshness, authorization and order timing are not recorded here",
        ],
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reports", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = aggregate(args.reports)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, indent=2)
    print("REPORTS=" + str(report["metrics"]["sessions"]))
    print("TRIGGER_RUNS=" + str(report["metrics"]["trigger_runs"]))
    print("OUTPUT=" + str(args.output))


if __name__ == "__main__":
    main()
