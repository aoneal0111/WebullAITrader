"""Read-only comparison of Warrior authorization/decision timing across trades.

Only consumes a previously reconciled entry-exit diagnosis JSON. The latest
nearby DECISION is not proven to be the snapshot consumed by the gateway.
This module cannot promote runtime policy or infer counterfactual outcomes.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from .capture_profit_audit import instant

VERSION = "WARRIOR_AUTHORIZATION_TIMING_COMPARISON_V1"
MAX_BYTES = 4_000_000
MAX_TRADES = 20


def _age(value):
    if value is None:
        return None
    try:
        age = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INVALID_CONFIRMATION_AGE") from exc
    if not age.is_finite() or age < 0:
        raise ValueError("INVALID_CONFIRMATION_AGE")
    return age


def compare_diagnosis(report, *, targets=("FSLY", "JAGX", "WFF", "AEHL")):
    rows = report.get("trades", [])
    if not 1 <= len(rows) <= MAX_TRADES:
        raise ValueError("INVALID_TRADE_COUNT")
    if report.get("runtime_policy_changed") is not False:
        raise ValueError("SOURCE_POLICY_SCOPE_UNKNOWN")
    if report.get("summary", {}).get("unreconciled") != 0:
        raise ValueError("UNRECONCILED_SOURCE")
    if len({(r.get("campaign"), r.get("lifecycle")) for r in rows}) != len(rows):
        raise ValueError("DUPLICATE_LIFECYCLE")
    observed = []
    for row in rows:
        if row.get("ledger_status") != "RECONCILED":
            raise ValueError("UNRECONCILED_SOURCE")
        details = row.get("entry_details", [])
        if row.get("symbol") not in targets:
            continue
        if row.get("entry_evidence_status") not in (
            "MOST_RECENT_DECISION_MATCHED", "MOST_RECENT_DECISION_MISMATCH",
        ):
            raise ValueError("ENTRY_PROVENANCE_INCOMPLETE")
        if len(details) != 1 or details[0].get("status") != "AUTHORIZATION_MATCHED":
            raise ValueError("AMBIGUOUS_ENTRY_ORDER")
        entry = details[0]
        authorization_at = instant(entry["authorization_time"])
        created_at = instant(entry["entry_created"])
        if created_at < authorization_at:
            raise ValueError("AUTHORIZATION_AFTER_ORDER")
        age = _age(entry.get("matching_confirmation_age_seconds"))
        # PR #119's exact-source evidence is independent of the latest
        # nearby confirmation. Historical diagnoses may lack these fields.
        source_status = entry.get(
            "source_decision_status", "SOURCE_DECISION_ID_NOT_RECORDED"
        )
        allowed_source_statuses = {
            "SOURCE_DECISION_ID_MATCHED",
            "SOURCE_DECISION_ID_AMBIGUOUS",
            "SOURCE_DECISION_ID_UNAVAILABLE",
            "SOURCE_DECISION_ID_NOT_RECORDED",
        }
        if source_status not in allowed_source_statuses:
            raise ValueError("INVALID_SOURCE_DECISION_STATUS")
        source_match = entry.get("source_decision_matches_authorization")
        source_age = _age(entry.get("source_decision_age_seconds"))
        if source_status == "SOURCE_DECISION_ID_MATCHED":
            if type(source_match) is not bool or source_age is None:
                raise ValueError("INCOMPLETE_MATCHED_SOURCE_DECISION")
        elif source_match is not None or source_age is not None:
            raise ValueError("UNSUPPORTED_SOURCE_DECISION_ASSERTION")
        pnl = Decimal(str(row["actual_closed_pnl"]))
        if not pnl.is_finite():
            raise ValueError("INVALID_PNL")
        quote_count = row.get("eligible_quotes")
        if not isinstance(quote_count, int) or quote_count < 0:
            raise ValueError("INVALID_QUOTE_COUNT")
        observed.append({
            "symbol": row["symbol"],
            "campaign": row["campaign"],
            "lifecycle": row["lifecycle"],
            "actual_closed_pnl": str(pnl),
            "outcome": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
            "authorization_time": authorization_at.isoformat(),
            "order_created_at": created_at.isoformat(),
            "authorization_to_order_ms": str(
                Decimal(str((created_at - authorization_at).total_seconds())) * 1000
            ),
            "authorized_setup": entry.get("authorized_setup"),
            "latest_decision_matches": entry.get("current_confirmation_matches"),
            "latest_decision_state": entry.get("current_state"),
            "latest_decision_setup": entry.get("current_setup"),
            "matching_confirmation_age_seconds": str(age) if age is not None else None,
            "missing_matching_confirmation": age is None,
            "source_decision_status": source_status,
            "source_decision_state": entry.get("source_decision_state")
                if source_status == "SOURCE_DECISION_ID_MATCHED" else None,
            "source_decision_setup": entry.get("source_decision_setup")
                if source_status == "SOURCE_DECISION_ID_MATCHED" else None,
            "source_decision_matches_authorization": source_match,
            "source_decision_age_seconds": (
                str(source_age) if source_age is not None else None
            ),
            "eligible_quotes": quote_count,
            "missing_interval_flags": row.get("missing_interval_flags"),
            "candidate_signal_at": row.get("candidate_signal_at"),
            "proven_gateway_snapshot_identity": False,
            "causal_defect_proven": False,
        })
    observed.sort(key=lambda r: (r["symbol"], r["lifecycle"]))
    found = {r["symbol"] for r in observed}
    missing = sorted(set(targets) - found)
    if missing:
        raise ValueError("MISSING_TARGET_SYMBOLS:" + ",".join(missing))
    ages = sorted(Decimal(r["matching_confirmation_age_seconds"])
                  for r in observed if r["matching_confirmation_age_seconds"] is not None)
    return {
        "version": VERSION,
        "scope": "NEARBY_PRE_AUTH_DECISION_COMPARISON_NOT_GATEWAY_SNAPSHOT_LINEAGE",
        "target_symbols": list(targets),
        "trades": observed,
        "summary": {
            "target_trades": len(observed),
            "winners": sum(r["outcome"] == "WIN" for r in observed),
            "losers": sum(r["outcome"] == "LOSS" for r in observed),
            "latest_decision_mismatches": sum(
                r["latest_decision_matches"] is False for r in observed
            ),
            "exact_source_matches": sum(
                r["source_decision_status"] == "SOURCE_DECISION_ID_MATCHED"
                and r["source_decision_matches_authorization"] is True
                for r in observed
            ),
            "exact_source_mismatches": sum(
                r["source_decision_status"] == "SOURCE_DECISION_ID_MATCHED"
                and r["source_decision_matches_authorization"] is False
                for r in observed
            ),
            "exact_source_unknown": sum(
                r["source_decision_status"] != "SOURCE_DECISION_ID_MATCHED"
                for r in observed
            ),
            "with_matching_confirmation_age": len(ages),
            "max_observed_matching_confirmation_age_seconds": (
                str(ages[-1]) if ages else None
            ),
            # Record correlation is not gateway snapshot instrumentation.
            "no_gateway_snapshot_lineage_proven": True,
        },
        "follow_up": (
            "Compare actual decision snapshot consumed by the authorization gate, "
            "the current setup state and confirmation age. Do not impose a "
            "blanket stale-confirmation rejection from nearby DECISION records."
        ),
        "runtime_policy_changed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnosis", type=Path)
    args = parser.parse_args(argv)
    with args.diagnosis.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        parser.error("Diagnosis exceeds 4 MB")
    report = compare_diagnosis(json.loads(raw.decode("utf-8-sig")))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
