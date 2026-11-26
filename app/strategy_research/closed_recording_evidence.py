"""Bounded closed-recording event-shape inventory, without exporting payloads.

Observations are not promoted to broker-confirmed fills. No runtime, provider,
database, or strategy is opened by this module.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

MAX_METADATA_BYTES = 256 * 1024
MAX_JOURNAL_FILES = 32
MAX_JOURNAL_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024
MAX_EVENTS = 50000
_ALLOWED = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_KEYS = ("event_type", "eventType", "type", "kind")
_ORDER_HINTS = frozenset({
    "ORDER_SUBMITTED", "ORDER_ACCEPTED", "ORDER_REJECTED",
    "ORDER_CANCELLED", "ORDER_CANCELED", "ORDER_FILLED",
    "ORDER_PARTIALLY_FILLED", "FILL", "PAPER_FILL", "TRADE_FILL",
    "STOP_TRIGGERED", "PROTECTION_SUBMITTED", "ORDER_UPDATED",
})


def _small_json(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("MISSING_OR_OVERSIZED_METADATA")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def inventory(pointer_path: Path) -> dict:
    result = {
        "scope": "CLOSED_RECORDING_EVENT_SHAPE_V1",
        "status": "UNVERIFIED",
        "closed_recording_verified": False,
        "file_count": 0, "line_count": 0, "candidate_event_lines": 0,
        "unknown_event_lines": 0, "invalid_lines": 0,
        "event_type_counts": {}, "candidate_type_counts": {},
        "authoritative_order_ledger_proven": False,
        "fills_reconciled": False, "protective_exits_verified": False,
        "network_requests": 0, "orders_submitted": 0,
        "runtime_modified": False,
        "limitations": [
            "Event-type names are hints only and may describe a decision rather than an executed fill",
            "A closed recording does not prove complete captured data or historical order reconciliation",
            "No raw payloads, symbols, personal identifiers, prices, or broker tokens are exported",
            "No ledger or executable cost metrics are synthesized from journal observations",
        ],
    }
    try:
        pointer = _small_json(Path(pointer_path))
        raw = pointer.get("session")
        if not isinstance(raw, str) or not 1 <= len(raw) <= 2048:
            raise ValueError("INVALID_SESSION_POINTER")
        session = Path(raw)
        if session.is_symlink() or not session.is_dir():
            result["status"] = "MISSING_SESSION"
            return result
        _small_json(session / "research-manifest.json")
        summary = _small_json(session / "research-summary.json")
        journal = summary.get("journal") if isinstance(summary, dict) else None
        if not (isinstance(journal, dict) and journal.get("closed") is True
                and journal.get("writer_alive") is False):
            result["status"] = "RECORDING_CLOSURE_UNPROVEN"
            return result
        result["closed_recording_verified"] = True
        root = session / "journal"
        if root.is_symlink() or not root.is_dir():
            result["status"] = "JOURNAL_UNAVAILABLE"
            return result
        files = sorted(root.glob("events-*.jsonl"))
        result["file_count"] = len(files)
        if not files:
            result["status"] = "NO_EVENT_FILES"
            return result
        if (len(files) > MAX_JOURNAL_FILES or any(p.is_symlink() or not p.is_file()
                for p in files) or sum(p.stat().st_size for p in files) > MAX_JOURNAL_BYTES):
            result["status"] = "JOURNAL_BOUNDS_EXCEEDED"
            return result
        counts = Counter()
        candidates = Counter()
        for path in files:
            with path.open("rb") as stream:
                for raw_line in stream:
                    result["line_count"] += 1
                    if result["line_count"] > MAX_EVENTS or len(raw_line) > MAX_LINE_BYTES:
                        result["status"] = "JOURNAL_BOUNDS_EXCEEDED"
                        return result
                    try:
                        event = json.loads(raw_line)
                        if not isinstance(event, dict):
                            raise ValueError("NOT_OBJECT")
                    except (ValueError, UnicodeDecodeError):
                        result["invalid_lines"] += 1
                        continue
                    event_type = next((event[k] for k in _KEYS
                        if isinstance(event.get(k), str) and _ALLOWED.fullmatch(event[k])), None)
                    if event_type is None:
                        result["unknown_event_lines"] += 1
                        continue
                    # Never expose arbitrary untrusted labels or nested values.
                    if event_type not in _ORDER_HINTS:
                        result["unknown_event_lines"] += 1
                        continue
                    counts[event_type] += 1
                    candidates[event_type] += 1
                    result["candidate_event_lines"] += 1
        result["event_type_counts"] = dict(sorted(counts.items()))
        result["candidate_type_counts"] = dict(sorted(candidates.items()))
        result["status"] = ("CANDIDATE_ORDER_EVENTS_UNVERIFIED"
                            if candidates else "NO_RECOGNIZED_ORDER_EVENTS")
        return result
    except (OSError, ValueError, TypeError, KeyError):
        result["status"] = "RECORDING_METADATA_INVALID_OR_UNAVAILABLE"
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording-pointer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("OUTPUT_MUST_NOT_EXIST")
    result = inventory(args.recording_pointer)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print("RECORDING_STATUS=" + result["status"])
    print("CLOSED_RECORDING_VERIFIED=" + str(result["closed_recording_verified"]))
    print("CANDIDATE_EVENT_LINES=" + str(result["candidate_event_lines"]))
    print("AUTHORITATIVE_LEDGER_PROVEN=False")
    print("REPORT=" + str(args.output))


if __name__ == "__main__":
    main()
