"""Select complete, nonoverlapping Warrior captures for offline analysis.

This is a research input selector, not a broker, file repair or policy gate.
All candidate files remain untouched. Missing or contradictory evidence is
reported rather than being synthesized.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from collections import Counter
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
    path_records = {
        key: tuple(_canonical(r) for r in paths
                   if (r.get("payload", {}).get("paper_campaign_id", "legacy-paper-campaign"),
                       r.get("payload", {}).get("lifecycle_id")) == key)
        for key in grouped
    }
    return {
        "identities": tuple(grouped),
        "authorization_count": quality,
        "context_count": len(contexts),
        "ledger_fingerprints": {
            key: sha256(_canonical(sorted(orders, key=lambda x: x["order_id"])).encode()).hexdigest()
            for key, orders in grouped.items()
        },
        "price_path_records": path_records,
        "price_path_fingerprints": {
            key: sha256(_canonical(records).encode()).hexdigest()
            for key, records in path_records.items()
        },
    }




def _contained_observations(shorter, longer):
    """Exact, multiplicity-preserving chronological subsequence.

    No quote is synthesized. Reject same-length disagreements or missing /
    altered earlier observations, including repeats and ordering differences.
    """
    if len(shorter) > len(longer):
        return False
    iterator = iter(longer)
    return all(any(other == record for other in iterator) for record in shorter)


def _consistent_path_copies(copies):
    """Accept exact subsequences or identical multisets with safe quote timing.

    The downstream price audit sorts quotes by recorded timestamp, making
    capture-order differences irrelevant only when there is no conflicting
    quote payload for the same observation timestamp. Fill timestamps are
    reconciled against the immutable order ledger independently.
    """
    ordered = sorted(copies, key=lambda pair: (-len(pair[1]), pair[0]))
    canonical = ordered[0][1]
    for _, records in ordered[1:]:
        if _contained_observations(records, canonical):
            continue
        if len(records) != len(canonical) or Counter(records) != Counter(canonical):
            return False
        # Same exact records, different order: only safe to ignore capture order
        # if equal-time quote observations cannot influence signal sequence.
        # Canonical records are JSON strings, preserving all payload fields.
        quote_by_time = {}
        for raw in canonical:
            record = json.loads(raw)
            if record.get("payload", {}).get("action") != "QUOTE":
                continue
            time = record.get("timestamp")
            old = quote_by_time.setdefault(time, raw)
            if old != raw:
                return False
    return True


def inspect_capture_conflicts(candidates):
    """Summarize duplicate discrepancies without printing trade payloads.

    Intentionally does not resolve mismatches. Exact ordered-subsequence
    compatibility is reported, never used to invent missing quotes.
    """
    if not 1 <= len(candidates) <= MAX_FILES:
        raise ValueError("INVALID_CAPTURE_COUNT")
    originals = {}
    duplicates = {}
    path_values = {}
    for path, data in candidates:
        info = describe(data)
        path = str(path)
        for key in info["identities"]:
            record = {
                "file": path,
                "ledger_digest": info["ledger_fingerprints"][key],
                "price_path_digest": info["price_path_fingerprints"][key],
                "context_records": info["context_count"],
                "matched_authorizations": info["authorization_count"][key],
                "order_count": sum(
                    order_key(o) == key for o in data.get("orders", ())
                ),
                "price_path_records": sum(
                    (r.get("payload", {}).get("paper_campaign_id", "legacy-paper-campaign"),
                     r.get("payload", {}).get("lifecycle_id")) == key
                    for r in data.get("capture_records", ())
                ),
            }
            duplicates.setdefault(key, []).append(record)
            path_values.setdefault(key, []).append(
                (path, info["price_path_records"][key])
            )
            originals[key] = True
    conflicts = []
    repeated = 0
    for key, variants in sorted(duplicates.items()):
        if len(variants) <= 1:
            continue
        repeated += 1
        ledger_differs = len({v["ledger_digest"] for v in variants}) > 1
        path_differs = len({v["price_path_digest"] for v in variants}) > 1
        if ledger_differs or path_differs:
            conflicts.append({
                "symbol": key[1].split("|")[1] if "|" in key[1] else None,
                "campaign": key[0],
                "lifecycle": key[1],
                "ledger_conflict": ledger_differs,
                "price_path_conflict": path_differs,
                "exact_subset_compatible": (
                    not ledger_differs
                    and _consistent_path_copies(path_values[key])
                ),
                "files": [{
                    k: v[k] for k in (
                        "file", "context_records", "matched_authorizations",
                        "order_count", "price_path_records",
                    )
                } for v in variants],
            })
    return {
        "version": "WARRIOR_CAPTURE_COPY_CONFLICT_DIAGNOSIS_V1",
        "unique_lifecycles": len(originals),
        "repeated_lifecycles": repeated,
        "conflicting_lifecycles": len(conflicts),
        "conflicts": conflicts,
        "selection_permitted": len(conflicts) == 0,
        "no_runtime_changes": True,
    }


def select_capture_copies(candidates):
    """Return selected files only if identities, fills and price paths agree.

    A single file containing several lifecycles is selected only if it
    contributes no overlapping lifecycle. Ledgers must be identical; paths
    must be exact ordered subsets or identical observation multisets with
    unambiguous quote ordering by timestamp. Files rank by authorization
    evidence then path coverage then context.
    """
    if not 1 <= len(candidates) <= MAX_FILES:
        raise ValueError("INVALID_CAPTURE_COUNT")
    records = []
    by_lifecycle = {}
    for path, data in candidates:
        info = describe(data)
        records.append((str(path), info))
        for key in info["identities"]:
            by_lifecycle.setdefault(key, []).append((str(path), info))
    for key, copies in by_lifecycle.items():
        if len({info["ledger_fingerprints"][key] for _, info in copies}) > 1:
            raise ValueError("CONFLICTING_LIFECYCLE_EVIDENCE")
        if not _consistent_path_copies([
            (path, info["price_path_records"][key]) for path, info in copies
        ]):
            raise ValueError("CONFLICTING_LIFECYCLE_EVIDENCE")
    universe = set(by_lifecycle)
    selected = []
    covered = set()
    skipped = []
    ordered = sorted(records, key=lambda item: (
        -sum(item[1]["authorization_count"].values()),
        -sum(v > 0 for v in item[1]["authorization_count"].values()),
        -sum(len(v) for v in item[1]["price_path_records"].values()),
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
        "evidence": "IDENTICAL_LEDGER_AND_EXACT_SUBSET_OR_UNAMBIGUOUS_MULTISET_PATH_REQUIRED",
        "runtime_changed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--inspect-conflicts", action="store_true")
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
    if args.inspect_conflicts:
        print(json.dumps(inspect_capture_conflicts(candidates), indent=2))
        return 0
    print(json.dumps(select_capture_copies(candidates), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
