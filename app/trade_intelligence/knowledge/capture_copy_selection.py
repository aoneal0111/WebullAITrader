"""Select complete, nonoverlapping Warrior captures for offline analysis.

This is a research input selector, not a broker, file repair or policy gate.
All candidate files remain untouched. Missing or contradictory evidence is
reported rather than being synthesized.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from .capture_profit_audit import order_key

MAX_FILES = 100
MAX_FILE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 80_000_000


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def describe(data):
    grouped = {}
    for order in data.get("orders", ()):
        campaign, life = order_key(order)
        if not str(life).startswith("WARRIOR_MOMENTUM_V1|"):
            continue
        grouped.setdefault((campaign, life), []).append(order)
    if not grouped:
        raise ValueError("NO_WARRIOR_LIFECYCLE")
    contexts = data.get("entry_context", {}).get("records", ())
    if contexts is None:
        contexts = ()
    if len(contexts) > 1000:
        raise ValueError("INPUT_RECORD_LIMIT")
    quality = {}
    for key, orders in grouped.items():
        campaign, life = key
        symbol = orders[0]["request"]["symbol"]
        authorization = [
            record for record in contexts
            if record.get("record_type") == "STATE_TRANSITION"
            and record.get("symbol") == symbol
            and record.get("payload", {}).get("to") == "AUTHORIZATION_EVALUATED"
            and record["payload"].get("lifecycle_id") == life
            and record["payload"].get("paper_campaign_id", campaign) == campaign
            and record["payload"].get("authorization_result") == "AUTHORIZED"
        ]
        quality[key] = len(authorization)
    paths = data.get("capture_records", ())
    return {
        "identities": tuple(grouped),
        "authorization_count": quality,
        "context_count": len(contexts),
        "ledger_fingerprints": {
            key: sha256(_canonical(sorted(orders, key=lambda x: x["order_id"])).encode()).hexdigest()
            for key, orders in grouped.items()
        },
        "price_path_fingerprints": {
            key: sha256(_canonical([
                r for r in paths
                if (r.get("payload", {}).get("paper_campaign_id", "legacy-paper-campaign"),
                    r.get("payload", {}).get("lifecycle_id")) == key
            ]).encode()).hexdigest()
            for key in grouped
        },
    }


def select_capture_copies(candidates):
    """Return selected files only if identities, fills and price paths agree.

    A single file containing several lifecycles is selected only if it
    contributes no overlapping lifecycle. Files are sorted by complete
    matching authorization evidence before context quantity; tie by path.
    """
    if not 1 <= len(candidates) <= MAX_FILES:
        raise ValueError("INVALID_CAPTURE_COUNT")
    records = []
    known = {}
    for path, data in candidates:
        info = describe(data)
        for key in info["identities"]:
            fingerprint = (info["ledger_fingerprints"][key],
                           info["price_path_fingerprints"][key])
            old = known.setdefault(key, fingerprint)
            if old != fingerprint:
                raise ValueError("CONFLICTING_LIFECYCLE_EVIDENCE")
        records.append((str(path), info))
    universe = set(known)
    selected = []
    covered = set()
    skipped = []
    ordered = sorted(records, key=lambda item: (
        -sum(item[1]["authorization_count"].values()),
        -sum(v > 0 for v in item[1]["authorization_count"].values()),
        -item[1]["context_count"],
        item[0],
    ))
    for path, info in ordered:
        keys = set(info["identities"])
        if keys & covered:
            skipped.append({"file": path, "reason": "DUPLICATE_LIFECYCLE"})
            continue
        selected.append(path)
        covered.update(keys)
    if covered != universe:
        raise ValueError("UNREPRESENTED_LIFECYCLES_AFTER_SELECTION")
    return {
        "selected_files": sorted(selected),
        "skipped": skipped,
        "unique_lifecycles": len(universe),
        "matching_authorization_coverage": sum(
            max(info["authorization_count"].get(key, 0) for _, info in records) > 0
            for key in universe
        ),
        "missing_matching_authorization": [
            {"campaign": key[0], "lifecycle": key[1]}
            for key in sorted(universe)
            if not any(info["authorization_count"].get(key, 0) for _, info in records)
        ],
        "evidence": "IDENTICAL_LEDGER_AND_CAPTURE_PATHS_REQUIRED",
        "runtime_changed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if len(args.inputs) > MAX_FILES:
        parser.error("Maximum 100 candidate copies")
    candidates, total = [], 0
    for path in args.inputs:
        with path.open("rb") as f:
            raw = f.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            parser.error("A capture exceeds 8 MB")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            parser.error("Combined copies exceed 80 MB")
        candidates.append((str(path), json.loads(raw.decode("utf-8-sig"))))
    print(json.dumps(select_capture_copies(candidates), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
