"""Evidence-linked closed Warrior entry/exit diagnosis; no policy promotion.

Per lifecycle: actual fill result, matching authorization evidence, latest
eligible entry decision, and sampled quote/profit giveback observations.
A nearby symbol/time context is NOT itself an exact causal authorization join.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path

from .capture_entry_audit import audit_entries
from .capture_profit_audit import audit_capture, instant, order_key

D = Decimal
VERSION = "WARRIOR_ENTRY_EXIT_DIAGNOSIS_V1"
MAX_FILES = 20
MAX_FILE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 20_000_000


def diagnose_captures(captures, *, activation_r=D("0.40"),
                      peak_retention=D("0.50"),
                      max_quote_age_seconds=D("5"),
                      extra_cost_per_side=D("0")):
    if not 1 <= len(captures) <= MAX_FILES:
        raise ValueError("INVALID_CAPTURE_COUNT")
    rows = []
    seen = set()
    for data in captures:
        exit_audit = audit_capture(
            data, activation_r=activation_r, peak_retention=peak_retention,
            max_quote_age_seconds=max_quote_age_seconds,
            extra_cost_per_side=extra_cost_per_side,
        )
        entry_audit = audit_entries(data)
        entries = {}
        for entry in entry_audit["entries"]:
            key = (entry["campaign"], entry["lifecycle"])
            entries.setdefault(key, []).append(entry)
        orders = {}
        for order in data["orders"]:
            key = order_key(order)
            orders.setdefault(key, []).append(order)
        for exit_row in exit_audit["lifecycles"]:
            key = (exit_row["campaign"], exit_row["lifecycle"])
            if key in seen:
                raise ValueError("DUPLICATE_LIFECYCLE_ACROSS_CAPTURES")
            seen.add(key)
            matching_entries = entries.get(key, [])
            buys = [
                fill for order in orders.get(key, [])
                if order["request"]["side"] == "BUY"
                for fill in order.get("fills", [])
            ]
            last_entry_fill = (
                max(instant(f["timestamp"]) for f in buys).isoformat()
                if buys else None
            )
            entry_states = [e["status"] for e in matching_entries]
            matched = [e for e in matching_entries
                       if e["status"] == "AUTHORIZATION_MATCHED"]
            decisions = [e for e in matched
                         if "current_confirmation_matches" in e]
            evidence_status = (
                "NO_ENTRY_RECORD" if not matching_entries
                else "NO_MATCHED_AUTHORIZATION" if not matched
                else "CURRENT_DECISION_UNAVAILABLE" if not decisions
                else "MOST_RECENT_DECISION_MATCHED" if all(
                    e["current_confirmation_matches"] for e in decisions
                ) else "MOST_RECENT_DECISION_MISMATCH"
            )
            # Last decision is not necessarily the exact one consumed by the
            # execution gate; do not infer that a mismatch caused the trade.
            point = exit_row.get("sampled_peak_total_profit")
            armed = exit_row.get("candidate_armed_at")
            signal = exit_row.get("first_observed_signal")
            rows.append({
                "campaign": key[0], "lifecycle": key[1],
                "symbol": exit_row["symbol"],
                "entry_complete_at": last_entry_fill,
                "ledger_status": exit_row["status"],
                "reconciliation_problems": exit_row["reconciliation_problems"],
                "entry_evidence_status": evidence_status,
                "entry_audit_statuses": entry_states,
                "matched_authorizations": len(matched),
                "entry_order_count": len(matching_entries),
                "entry_details": [{
                    "order_id": e["order_id"],
                    "status": e["status"],
                    "entry_created": e["entry_created"],
                    "authorization_time": e.get("authorization_time"),
                    "authorized_setup": e.get("authorized_setup"),
                    "authorized_trigger": e.get("authorized_trigger"),
                    "authorized_stop": e.get("authorized_stop"),
                    "current_state": e.get("current_state"),
                    "current_setup": e.get("current_setup"),
                    "current_confirmation_matches": e.get(
                        "current_confirmation_matches"),
                    "matching_confirmation_age_seconds": e.get(
                        "matching_confirmation_age_seconds"),
                    "current_structural_invalidation": e.get(
                        "current_structural_invalidation"),
                } for e in matching_entries],
                "actual_closed_pnl": exit_row.get("actual_closed_pnl"),
                "recorded_commissions": exit_row.get("recorded_commissions"),
                "initial_price_risk": exit_row.get("initial_price_risk"),
                "eligible_quotes": exit_row.get("eligible_quotes"),
                "excluded_quotes": dict(exit_row.get("excluded_quotes", {})),
                "missing_interval_flags": exit_row.get("missing_interval_flags"),
                "sampled_peak_profit": (
                    None if point is None else point["total_profit_mark"]
                ),
                "sampled_peak_at": (
                    None if point is None else point["evaluated_at"].isoformat()
                ),
                "candidate_armed_at": (
                    None if armed is None else armed["evaluated_at"].isoformat()
                ),
                "candidate_signal_at": (
                    None if signal is None else signal["evaluated_at"].isoformat()
                ),
                "candidate_signal_bid": (
                    None if signal is None else signal["bid"]
                ),
                "hypothetical_fill_pnl": None,
                "causal_entry_defect_proven": False,
            })
    rows.sort(key=lambda r: (
        r["entry_complete_at"] or "",
        r["campaign"], r["lifecycle"],
    ))
    closed = [r for r in rows if r["ledger_status"] == "RECONCILED"]
    winners = [r for r in closed if r["actual_closed_pnl"] > 0]
    losers = [r for r in closed if r["actual_closed_pnl"] < 0]
    return {
        "version": VERSION,
        "configuration": {
            "activation_r": str(activation_r),
            "peak_retention": str(peak_retention),
            "max_quote_age_seconds": str(max_quote_age_seconds),
            "extra_cost_per_side": str(extra_cost_per_side),
        },
        "capture_files": len(captures),
        "unique_lifecycles": len(rows),
        "summary": {
            "reconciled": len(closed),
            "unreconciled": len(rows) - len(closed),
            "winners": len(winners),
            "losers": len(losers),
            "flat": len(closed) - len(winners) - len(losers),
            "actual_closed_net_pnl": sum(
                (r["actual_closed_pnl"] for r in closed), D(0)
            ),
            "missing_matching_authorization": sum(
                r["entry_evidence_status"] in {
                    "NO_ENTRY_RECORD", "NO_MATCHED_AUTHORIZATION",
                } for r in closed
            ),
            "missing_current_decision": sum(
                r["entry_evidence_status"] == "CURRENT_DECISION_UNAVAILABLE"
                for r in closed
            ),
            "latest_decision_mismatch": sum(
                r["entry_evidence_status"] == "MOST_RECENT_DECISION_MISMATCH"
                for r in closed
            ),
            "winner_signals": sum(
                r["candidate_signal_at"] is not None for r in winners
            ),
            "loser_signals": sum(
                r["candidate_signal_at"] is not None for r in losers
            ),
        },
        "trades": rows,
        "scope": (
            "RETROSPECTIVE_NEARBY_ENTRY_EVIDENCE_AND_OBSERVED_EXIT_SIGNALS_"
            "NOT_CAUSAL_PROOF_OR_COUNTERFACTUAL_EXECUTION"
        ),
        "runtime_policy_changed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--activation-r", type=D, default=D("0.40"))
    parser.add_argument("--peak-retention", type=D, default=D("0.50"))
    parser.add_argument("--max-quote-age-seconds", type=D, default=D("5"))
    parser.add_argument("--extra-cost-per-side", type=D, default=D("0"))
    args = parser.parse_args(argv)
    if len(args.inputs) > MAX_FILES:
        parser.error("At most 20 capture files")
    captures = []
    total = 0
    for path in args.inputs:
        with path.open("rb") as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            parser.error("Capture exceeds 8 MB")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            parser.error("Combined capture size exceeds 20 MB")
        captures.append(json.loads(raw.decode("utf-8-sig")))
    print(json.dumps(diagnose_captures(
        captures, activation_r=args.activation_r,
        peak_retention=args.peak_retention,
        max_quote_age_seconds=args.max_quote_age_seconds,
        extra_cost_per_side=args.extra_cost_per_side,
    ), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
