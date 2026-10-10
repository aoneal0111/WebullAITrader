"""Read-only entry provenance audit; evidence comparison, not a trading policy."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path

from .capture_profit_audit import instant, order_key


def audit_entries(data):
    orders = data.get("orders", [])
    records = data.get("entry_context", {}).get("records", [])
    if len(orders) > 500 or len(records) > 1000:
        raise ValueError("INPUT_RECORD_LIMIT")
    result = []
    for order in orders:
        request = order["request"]
        campaign, lifecycle = order_key(order)
        if (request["side"] != "BUY" or Decimal(order["filled_quantity"]) <= 0
                or not str(lifecycle).startswith("WARRIOR_MOMENTUM_V1|")):
            continue
        created = instant(order["created_at"])
        symbol = request["symbol"]
        authorizations = []
        for record in records:
            payload = record["payload"]
            if (record.get("symbol") == symbol and record.get("record_type") == "STATE_TRANSITION"
                    and payload.get("to") == "AUTHORIZATION_EVALUATED"
                    and payload.get("lifecycle_id") == lifecycle
                    and payload.get("paper_campaign_id", campaign) == campaign
                    and payload.get("authorization_result") == "AUTHORIZED"):
                stamp = instant(payload.get("decision_timestamp") or record["timestamp"])
                if stamp <= created:
                    authorizations.append((stamp, payload))
        row = {"campaign": campaign, "lifecycle": lifecycle, "symbol": symbol,
               "order_id": order["order_id"], "entry_created": created.isoformat(),
               "status": "AUTHORIZATION_EVIDENCE_MISSING"}
        if not authorizations:
            result.append(row)
            continue
        authorization_time, authorization = max(authorizations, key=lambda item: item[0])
        decisions = []
        for record in records:
            payload = record["payload"]
            if record.get("symbol") != symbol or record.get("record_type") != "DECISION":
                continue
            if payload.get("paper_campaign_id", campaign) != campaign:
                continue
            # Provider record timestamps can precede the local evaluation. Never
            # treat a later evaluation as evidence available at authorization.
            if not payload.get("evaluation_timestamp"):
                continue
            if (not authorization.get("configuration_fingerprint")
                    or payload.get("configuration_fingerprint") != authorization["configuration_fingerprint"]):
                continue
            stamp = instant(payload["evaluation_timestamp"])
            if stamp <= authorization_time:
                decisions.append((stamp, payload))
        row.update(status="AUTHORIZATION_MATCHED", authorization_time=authorization_time.isoformat(),
                   authorized_setup=authorization.get("setup"), authorized_trigger=authorization.get("trigger"),
                   authorized_stop=authorization.get("structural_stop"))
        if decisions:
            stamp, current = max(decisions, key=lambda item: item[0])
            setup = current.get("setup") or {}
            row.update(latest_decision_time=stamp.isoformat(), current_state=setup.get("state", "NO_SETUP"),
                       current_setup=setup.get("type"), current_trigger=setup.get("trigger"),
                       current_structural_invalidation=current.get("canonical_setup_evidence", {}).get("structural_invalidation", []))
            matching = [(t, p) for t, p in decisions if (p.get("setup") or {}).get("state") == "TRIGGERED"
                        and (p.get("setup") or {}).get("type") == authorization.get("setup")
                        and (p.get("setup") or {}).get("trigger") is not None
                        and authorization.get("trigger") is not None
                        and Decimal(p["setup"]["trigger"]) == Decimal(authorization["trigger"])]
            if matching:
                confirmed, _ = max(matching, key=lambda item: item[0])
                row.update(latest_matching_confirmation=confirmed.isoformat(),
                           matching_confirmation_age_seconds=str(Decimal(str((authorization_time - confirmed).total_seconds()))))
            row["current_confirmation_matches"] = bool(
                setup.get("state") == "TRIGGERED" and setup.get("type") == authorization.get("setup")
                and setup.get("trigger") is not None and authorization.get("trigger") is not None
                and Decimal(setup["trigger"]) == Decimal(authorization["trigger"]))
        else:
            row["status"] = "CURRENT_DECISION_EVIDENCE_MISSING"
        result.append(row)
    return {"version": "CAPTURE_ENTRY_EVIDENCE_V1", "entries": result,
            "scope": "NEARBY_DECISIONS_NOT_PROVEN_CACHE_LINEAGE_NO_COUNTERFACTUAL_PNL",
            "runtime_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    with args.capture.open("rb") as handle:
        raw = handle.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("INPUT_BYTE_LIMIT")
    print(json.dumps(audit_entries(json.loads(raw)), indent=2))


if __name__ == "__main__":
    main()
