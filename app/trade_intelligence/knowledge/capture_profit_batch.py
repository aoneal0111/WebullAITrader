"""Bounded retrospective Warrior profit-signal batch across closed lifecycles.

Read-only, offline, no simulated execution, no policy promotion. One row per
unique campaign/lifecycle across captures; duplicated lifecycles are rejected.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path

from .capture_profit_matrix import ACTIVATIONS, RETENTIONS, compare_capture

D = Decimal
VERSION = "WARRIOR_PROFIT_RETENTION_BATCH_V1"
MAX_FILES = 20
MAX_FILE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 20_000_000


def summarize_captures(captures, *, extra_cost_per_side=D("0"),
                       max_quote_age_seconds=D("5")):
    """Aggregate observed candidate signals, never inferred exits or fills."""
    if not 1 <= len(captures) <= MAX_FILES:
        raise ValueError("INVALID_CAPTURE_COUNT")
    matrix_reports = [
        compare_capture(
            data, extra_cost_per_side=extra_cost_per_side,
            max_quote_age_seconds=max_quote_age_seconds,
        )
        for data in captures
    ]
    seen = set()
    for report in matrix_reports:
        for lifecycle in report["scenarios"][0]["lifecycles"]:
            key = (lifecycle["campaign"], lifecycle["lifecycle"])
            if key in seen:
                raise ValueError("DUPLICATE_LIFECYCLE_ACROSS_CAPTURES")
            seen.add(key)
    scenarios = []
    for index, (activation, retention) in enumerate(
        (a, r) for a in ACTIVATIONS for r in RETENTIONS
    ):
        rows = [
            row
            for report in matrix_reports
            for row in report["scenarios"][index]["lifecycles"]
        ]
        winners = [r for r in rows if r["status"] == "RECONCILED"
                   and r["actual_closed_pnl"] > 0]
        losers = [r for r in rows if r["status"] == "RECONCILED"
                  and r["actual_closed_pnl"] < 0]
        flat = [r for r in rows if r["status"] == "RECONCILED"
                and r["actual_closed_pnl"] == 0]
        reconciled = winners + losers + flat
        scenarios.append({
            "activation_r": activation,
            "peak_retention": retention,
            "reconciled": len(reconciled),
            "excluded_unreconciled": len(rows) - len(reconciled),
            "actual_winners": len(winners),
            "actual_losers": len(losers),
            "actual_flat": len(flat),
            "actual_net_pnl": sum(
                (r["actual_closed_pnl"] for r in reconciled), D(0)
            ),
            "observed_signals": sum(
                r["first_signal_at"] is not None for r in reconciled
            ),
            "winning_trade_signals": sum(
                r["first_signal_at"] is not None for r in winners
            ),
            "losing_trade_signals": sum(
                r["first_signal_at"] is not None for r in losers
            ),
            "flat_trade_signals": sum(
                r["first_signal_at"] is not None for r in flat
            ),
            "armed_without_signal": sum(
                r["armed_at"] is not None and r["first_signal_at"] is None
                for r in reconciled
            ),
            "unknown_execution_fill_count": None,
            "hypothetical_net_pnl": None,
        })
    return {
        "version": VERSION,
        "capture_files": len(captures),
        "unique_lifecycles": len(seen),
        "scenarios": scenarios,
        "evidence": "ACTUAL_RECONCILED_FILLS_AND_SAMPLED_QUOTE_SIGNALS_ONLY",
        "limits": [
            "SAMPLED_BIDS_ARE_NOT_ORDER_ACKNOWLEDGEMENTS",
            "SIGNALS_CANNOT_BE_INTERPRETED_AS_WINNING_EXITS",
            "COUNTERFACTUAL_PNL_AND_EXECUTION_FEASIBILITY_UNAVAILABLE",
            "RETROSPECTIVE_SAMPLE_NOT_OUT_OF_SAMPLE",
        ],
        "runtime_policy_changed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path,
                        help="Explicit capture JSON files (do not include manifests or matrix outputs)")
    parser.add_argument("--extra-cost-per-share-per-side", type=D, default=D("0"))
    parser.add_argument("--max-quote-age-seconds", type=D, default=D("5"))
    args = parser.parse_args(argv)
    if len(args.inputs) > MAX_FILES:
        parser.error("At most 20 captures per batch")
    payloads = []
    total = 0
    for path in args.inputs:
        with path.open("rb") as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            parser.error("One capture exceeds the 8 MB limit")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            parser.error("Capture inputs exceed 20 MB total")
        payloads.append(json.loads(raw.decode("utf-8-sig")))
    report = summarize_captures(
        payloads, extra_cost_per_side=args.extra_cost_per_share_per_side,
        max_quote_age_seconds=args.max_quote_age_seconds,
    )
    print(json.dumps(report, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
