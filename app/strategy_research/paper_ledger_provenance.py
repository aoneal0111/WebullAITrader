"""Read-only provenance inventory for explicitly supplied PAPER ledger candidates.

This tool never discovers candidates by recursive filesystem scans, creates a
SQLite database, restores accounts, or infers that a test ledger is authoritative.
Only safe metadata, schema names and aggregate order counts are emitted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3

from app.read_only_sqlite import read_database

MAX_CANDIDATES = 12
MAX_METADATA_BYTES = 262_144
EXPECTED_PAPER_TABLES = frozenset({"metadata", "orders", "paper_campaigns"})
_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z_0-9]{0,80}$")


def _tables(db):
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    if len(rows) > 128:
        raise ValueError("TABLE_COUNT_BOUND")
    return {row[0] for row in rows if isinstance(row[0], str)}


def inspect_ledger(path: Path) -> dict:
    """Read an existing candidate only, refusing non-ledgers without mutation."""
    path = Path(path)
    item = {"filename": path.name, "exists": False,
            "classification": "MISSING", "verified_paper_identity": False,
            "authoritative_runtime_proven": False}
    if path.is_symlink() or not path.is_file():
        item["classification"] = "NOT_REGULAR_EXISTING_FILE"
        return item
    item["exists"] = True
    try:
        if path.stat().st_size > 2_000_000_000:
            raise ValueError("FILE_SIZE_BOUND")
        with read_database(path) as db:
            tables = _tables(db)
            item["table_count"] = len(tables)
            item["expected_tables_present"] = sorted(EXPECTED_PAPER_TABLES & tables)
            if not EXPECTED_PAPER_TABLES <= tables:
                item["classification"] = "OTHER_SQLITE_SCHEMA"
                return item
            cols = {row[1] for row in db.execute("PRAGMA table_info(metadata)")}
            ordercols = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
            if not {"key", "value"} <= cols or not {"order_id", "payload"} <= ordercols:
                item["classification"] = "UNEXPECTED_PAPER_SCHEMA"
                return item
            # Read only bounded metadata keys; never print account IDs or tokens.
            metadata = db.execute(
                "SELECT key,value FROM metadata WHERE key IN "
                "('environment','schema_version','active_campaign_id') LIMIT 4"
            ).fetchall()
            env = {key: value for key, value in metadata if key == "environment"}
            item["environment_paper"] = env.get("environment") == "PAPER"
            if not item["environment_paper"]:
                item["classification"] = "NON_PAPER_IDENTITY"
                return item
            orders = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            campaigns = db.execute("SELECT COUNT(*) FROM paper_campaigns").fetchone()[0]
            item.update(order_count=orders, campaign_count=campaigns,
                        verified_paper_identity=True,
                        classification="PAPER_SCHEMA_WITH_ORDERS" if orders else "EMPTY_PAPER_SCHEMA")
            return item
    except (sqlite3.Error, OSError, ValueError, TypeError):
        item["classification"] = "UNREADABLE_OR_INVALID_SQLITE"
        return item


def inspect_recording_pointer(pointer_path: Path) -> dict:
    path = Path(pointer_path)
    result = {"exists": False, "closed_summary": False, "linked_session_available": False,
              "classification": "NO_RECORDING_POINTER"}
    if path.is_symlink() or not path.is_file():
        return result
    result["exists"] = True
    if path.stat().st_size > MAX_METADATA_BYTES:
        result["classification"] = "POINTER_SIZE_BOUND"
        return result
    try:
        pointer = json.loads(path.read_text(encoding="utf-8-sig"))
        raw = pointer.get("session")
        if not isinstance(raw, str) or not raw or len(raw) > 2048:
            raise ValueError("INVALID_SESSION_REFERENCE")
        session = Path(raw)
        result["linked_session_available"] = session.is_dir()
        if not result["linked_session_available"]:
            result["classification"] = "REFERENCED_SESSION_MISSING"
            return result
        manifest = session / "research-manifest.json"
        summary = session / "research-summary.json"
        if (manifest.is_symlink() or not manifest.is_file() or
                manifest.stat().st_size > MAX_METADATA_BYTES or
                summary.is_symlink() or not summary.is_file() or
                summary.stat().st_size > MAX_METADATA_BYTES):
            result["classification"] = "INCOMPLETE_OR_OVERSIZED_RECORDING"
            return result
        json.loads(manifest.read_text(encoding="utf-8-sig"))
        final = json.loads(summary.read_text(encoding="utf-8-sig"))
        journal = final.get("journal") if isinstance(final, dict) else None
        result["closed_summary"] = bool(isinstance(journal, dict)
                                        and journal.get("closed") is True
                                        and journal.get("writer_alive") is False)
        result["classification"] = (
            "CLOSED_RECORDING_NOT_ORDER_LEDGER_PROOF" if result["closed_summary"]
            else "RECORDING_CLOSURE_UNPROVEN")
    except (OSError, ValueError, TypeError, KeyError):
        result["classification"] = "INVALID_OR_UNAVAILABLE_RECORDING_METADATA"
    return result


def inventory(paths, *, recording_pointer=None):
    if not 1 <= len(paths) <= MAX_CANDIDATES:
        raise ValueError("CANDIDATE_COUNT_BOUND")
    results = [inspect_ledger(Path(p)) for p in paths]
    return {
        "scope": "OFFLINE_ATLAS_PAPER_LEDGER_PROVENANCE_V1",
        "candidates": results,
        "recording": (inspect_recording_pointer(Path(recording_pointer))
                      if recording_pointer is not None else
                      {"classification": "NOT_SUPPLIED"}),
        "authoritative_ledger_identified": False,
        "orders": 0, "network_requests": 0, "runtime_modified": False,
        "limitations": [
            "PAPER schema identity is not evidence that a database belongs to the target runtime",
            "An empty expected ledger is not evidence that historical fills were deleted",
            "A closed journal may contain observations but is not the same as a reconciled fill ledger",
            "No unprovided directories are searched; test fixtures are not promoted to runtime records",
            "No broker reads, account restoration, database creation, or strategy changes",
        ],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True, action="append")
    p.add_argument("--recording-pointer", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args(argv)
    if a.output.exists():
        p.error("OUTPUT_MUST_NOT_EXIST")
    report = inventory(a.candidate, recording_pointer=a.recording_pointer)
    with a.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print("CANDIDATES=" + str(len(report["candidates"])))
    print("PAPER_SCHEMA_CANDIDATES=" + str(sum(c["verified_paper_identity"] for c in report["candidates"])))
    print("AUTHORITATIVE_LEDGER_PROVEN=False")
    print("REPORT=" + str(a.output))


if __name__ == "__main__":
    main()
