"""Offline comparison of observed Warrior protection signals across thresholds.

Never alters positions, sends orders, estimates fills, or promotes thresholds.
Every scenario reuses the canonical reconciliation and chronological quote
validation from capture_profit_audit.audit_capture.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path

from .capture_profit_audit import audit_capture

D = Decimal
VERSION = "WARRIOR_PROFIT_RETENTION_EVIDENCE_MATRIX_V1"
ACTIVATIONS = (D("0.25"), D("0.40"), D("0.50"), D("0.75"), D("1.50"))
RETENTIONS = (D("0.50"), D("0.75"))
MAX_INPUT_BYTES = 8_000_000


def compare_capture(data, *, max_quote_age_seconds=D("5"),
                    extra_cost_per_side=D("0")):
    """Evaluate fixed research scenarios, using no prospective execution model."""
    baseline = audit_capture(
        data, activation_r=D("0.75"), peak_retention=D("0.50"),
        max_quote_age_seconds=max_quote_age_seconds,
        extra_cost_per_side=extra_cost_per_side,
    )
    original = {
        (row["campaign"], row["lifecycle"]): row for row in baseline["lifecycles"]
    }
    scenarios = []
    for activation in ACTIVATIONS:
        for retention in RETENTIONS:
            review = audit_capture(
                data, activation_r=activation, peak_retention=retention,
                max_quote_age_seconds=max_quote_age_seconds,
                extra_cost_per_side=extra_cost_per_side,
            )
            rows = []
            for lifecycle in review["lifecycles"]:
                identity = (lifecycle["campaign"], lifecycle["lifecycle"])
                base = original[identity]
                if lifecycle["status"] != base["status"]:
                    raise ValueError("INCONSISTENT_RECONCILIATION_STATUS")
                if lifecycle["status"] == "RECONCILED" and (
                    lifecycle["actual_closed_pnl"] != base["actual_closed_pnl"]
                    or lifecycle["eligible_quotes"] != base["eligible_quotes"]
                ):
                    raise ValueError("INCONSISTENT_ACTUAL_EVIDENCE")
                armed = lifecycle.get("candidate_armed_at")
                signal = lifecycle.get("first_observed_signal")
                rows.append({
                    "campaign": lifecycle["campaign"],
                    "lifecycle": lifecycle["lifecycle"],
                    "symbol": lifecycle["symbol"],
                    "status": lifecycle["status"],
                    "reconciliation_problems": lifecycle["reconciliation_problems"],
                    "actual_closed_pnl": lifecycle.get("actual_closed_pnl"),
                    "eligible_quotes": lifecycle.get("eligible_quotes"),
                    "missing_interval_flags": lifecycle.get("missing_interval_flags"),
                    "armed_at": None if armed is None else armed["evaluated_at"],
                    "first_signal_at": None if signal is None else signal["evaluated_at"],
                    "signal_bid": None if signal is None else signal["bid"],
                    "sampled_peak_profit": (
                        None if lifecycle.get("sampled_peak_total_profit") is None
                        else lifecycle["sampled_peak_total_profit"]["total_profit_mark"]
                    ),
                    "hypothetical_fill_price": None,
                    "hypothetical_pnl": None,
                })
            scenarios.append({
                "activation_r": activation,
                "peak_retention": retention,
                "reconciled": sum(r["status"] == "RECONCILED" for r in rows),
                "not_reconciled": sum(r["status"] != "RECONCILED" for r in rows),
                "armed": sum(r["armed_at"] is not None for r in rows),
                "observed_signals": sum(r["first_signal_at"] is not None for r in rows),
                "lifecycles": rows,
            })
    return {
        "version": VERSION,
        "source_lifecycles": len(original),
        "baseline_activation_r": D("0.75"),
        "scenarios": scenarios,
        "evidence": "RECORDED_FILL_LEDGER_AND_SAMPLED_BID_QUOTES_ONLY",
        "not_established": [
            "ACTUAL_REACHABLE_EXIT_ORDER",
            "FILL_PRICE_OR_LIQUIDITY",
            "COUNTERFACTUAL_PNL_OR_WIN_RATE",
            "FUTURE_STRATEGY_PROFITABILITY",
            "FIRST_TRUE_THRESHOLD_CROSSING_BETWEEN_QUOTES",
        ],
        "runtime_execution_enabled": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--max-quote-age-seconds", type=D, default=D("5"))
    parser.add_argument("--extra-cost-per-share-per-side", type=D, default=D("0"))
    args = parser.parse_args(argv)
    with args.input.open("rb") as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        parser.error("Input must be at most 8 MB")
    review = compare_capture(
        json.loads(raw.decode("utf-8-sig")),
        max_quote_age_seconds=args.max_quote_age_seconds,
        extra_cost_per_side=args.extra_cost_per_share_per_side,
    )
    print(json.dumps(review, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
