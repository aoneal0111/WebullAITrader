"""Read-only observed entry-stage intervals from reconciled Warrior PAPER diagnoses.

Intervals are chronology checks, not proof of causal delay or executable quotes.
Never infer scanner discovery from the candidate signal or nearby decision.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

VERSION = "WARRIOR_PAPER_ENTRY_INTERVALS_V1"
MAX_BYTES = 4_000_000
MAX_TRADES = 100


def _instant(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return parsed


def _interval(a, b):
    if a is None or b is None:
        return None
    milliseconds = round((b - a).total_seconds() * 1000, 3)
    if milliseconds < 0:
        raise ValueError("REVERSED_STAGE_TIMESTAMPS")
    return milliseconds


def summarize(report):
    if report.get("version") != "WARRIOR_ENTRY_EXIT_DIAGNOSIS_V1":
        raise ValueError("RECONCILED_ENTRY_DIAGNOSIS_REQUIRED")
    if report.get("runtime_policy_changed") is not False:
        raise ValueError("SOURCE_POLICY_SCOPE_UNKNOWN")
    trades = report.get("trades")
    if not isinstance(trades, list) or not 1 <= len(trades) <= MAX_TRADES:
        raise ValueError("TRADE_COUNT_BOUND")
    identities = set()
    rows = []
    counts = Counter()
    for trade in trades:
        identity = (trade.get("campaign"), trade.get("lifecycle"))
        if not all(isinstance(x, str) and x for x in identity) or identity in identities:
            raise ValueError("MISSING_OR_DUPLICATE_LIFECYCLE")
        identities.add(identity)
        if trade.get("ledger_status") != "RECONCILED":
            counts["UNRECONCILED_EXCLUDED"] += 1
            continue
        details = trade.get("entry_details")
        if not isinstance(details, list) or len(details) != 1:
            counts["AMBIGUOUS_ENTRY_ORDER_EXCLUDED"] += 1
            continue
        entry = details[0]
        if entry.get("status") != "AUTHORIZATION_MATCHED":
            counts["AUTHORIZATION_UNMATCHED_EXCLUDED"] += 1
            continue
        authorization = _instant(entry.get("authorization_time"))
        created = _instant(entry.get("entry_created"))
        if authorization is None or created is None:
            counts["ENTRY_TIMESTAMP_MISSING_EXCLUDED"] += 1
            continue
        # Candidate signal is retrospective evidence, not scanner discovery.
        signal = _instant(trade.get("candidate_signal_at"))
        filled = _instant(trade.get("entry_complete_at"))
        # A sampled signal can be unrelated to exact gate source. Exclude
        # it from stage duration if chronology or identity is not proven.
        source_status = entry.get("source_decision_status")
        source_matched = (
            source_status == "SOURCE_DECISION_ID_MATCHED"
            and entry.get("source_decision_matches_authorization") is True
        )
        source_time = _instant(entry.get("source_decision_timestamp")) if source_matched else None
        source_to_authorization = _interval(source_time, authorization)
        authorization_to_order = _interval(authorization, created)
        order_to_fill = _interval(created, filled)
        rows.append({
            "symbol": trade.get("symbol"),
            "campaign": identity[0],
            "lifecycle": identity[1],
            "source_lineage": "SOURCE_DECISION_MATCHED" if source_matched else "UNPROVEN",
            "source_to_authorization_ms": source_to_authorization,
            "authorization_to_order_ms": authorization_to_order,
            "order_to_last_entry_fill_ms": order_to_fill,
            "nearby_candidate_signal_at": None if signal is None else signal.isoformat(),
            "candidate_signal_to_order_ms": None,
            "scanner_discovery_at": None,
            "scanner_to_order_ms": None,
            "gateway_snapshot_identity_proven": False,
            "actual_closed_pnl": str(trade.get("actual_closed_pnl")),
        })
        counts["RECONCILED_MATCHED_ENTRY"] += 1
        if source_to_authorization is not None:
            counts["EXACT_SOURCE_DECISION_TIMING_AVAILABLE"] += 1
        else:
            counts["EXACT_SOURCE_DECISION_TIMING_UNAVAILABLE"] += 1
        if filled is None:
            counts["FILL_COMPLETION_TIMESTAMP_MISSING"] += 1
    return {
        "version": VERSION,
        "counts": dict(counts),
        "entries": rows,
        "orders": 0,
        "network_requests": 0,
        "runtime_policy_changed": False,
        "limitations": [
            "No scanner first-seen timestamp or scanner-to-order latency established",
            "Candidate signal timestamps are nearby forensic evidence, not exact order lineage",
            "Source decision ID match is not proof of the gateway snapshot consumed",
            "Order creation and fill timing are observed historical PAPER events, not executable counterfactuals",
            "Reconciled filled trades are subject to survivorship selection; rejected/unsubmitted opportunities absent",
            "No inferred missed-trade PnL or policy recommendation",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnosis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.diagnosis.is_symlink() or not args.diagnosis.is_file() or args.diagnosis.stat().st_size > MAX_BYTES:
        raise ValueError("BOUNDED_DIAGNOSIS_REQUIRED")
    source = json.loads(args.diagnosis.read_text(encoding="utf-8-sig"))
    report = summarize(source)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2)
    print("MATCHED_ENTRIES=" + str(len(report["entries"])))
    print("OUTPUT=" + str(args.output))


if __name__ == "__main__":
    main()
