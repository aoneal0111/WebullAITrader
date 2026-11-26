"""No ledger creation, authority guessing, or runtime mutation during inventory."""
import json
from pathlib import Path
import sqlite3

import pytest

from app.strategy_research.paper_ledger_provenance import (
    inspect_ledger, inspect_recording_pointer, inventory,
)


def make_ledger(path, *, environment="PAPER", order_count=0):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE orders(order_id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        db.execute("CREATE TABLE paper_campaigns(campaign_id TEXT PRIMARY KEY,status TEXT,"
                   "started_at TEXT,ended_at TEXT,reason TEXT)")
        db.execute("INSERT INTO metadata VALUES('environment',?)", (environment,))
        for n in range(order_count):
            db.execute("INSERT INTO orders VALUES (?, '{}')", (str(n),))


def test_missing_path_never_creates_database(tmp_path):
    missing = tmp_path / "paper-execution.sqlite3"
    result = inspect_ledger(missing)
    assert result["classification"] == "NOT_REGULAR_EXISTING_FILE"
    assert not missing.exists()


def test_paper_order_count_does_not_establish_runtime_authority(tmp_path):
    ledger = tmp_path / "paper-execution.sqlite3"
    make_ledger(ledger, order_count=4)
    before = ledger.read_bytes()
    result = inventory([ledger])
    assert result["candidates"][0]["classification"] == "PAPER_SCHEMA_WITH_ORDERS"
    assert result["candidates"][0]["order_count"] == 4
    assert result["candidates"][0]["verified_paper_identity"] is True
    assert not result["authoritative_ledger_identified"]
    assert ledger.read_bytes() == before


def test_empty_paper_schema_distinct_from_missing_and_lost_history(tmp_path):
    ledger = tmp_path / "empty.db"
    make_ledger(ledger)
    row = inspect_ledger(ledger)
    assert row["classification"] == "EMPTY_PAPER_SCHEMA"
    assert row["order_count"] == 0


def test_wrong_environment_is_rejected(tmp_path):
    ledger = tmp_path / "other.db"
    make_ledger(ledger, environment="LIVE", order_count=1)
    row = inspect_ledger(ledger)
    assert row["classification"] == "NON_PAPER_IDENTITY"
    assert row["verified_paper_identity"] is False
    assert "order_count" not in row


def test_execution_mutation_store_is_not_an_order_ledger(tmp_path):
    path = tmp_path / "execution_database_path.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE execution_metadata(key TEXT)")
        db.execute("CREATE TABLE mutations(id TEXT)")
    result = inspect_ledger(path)
    assert result["classification"] == "OTHER_SQLITE_SCHEMA"


def test_invalid_sqlite_and_symlink_are_fail_closed(tmp_path):
    p = tmp_path / "invalid.db"
    p.write_bytes(b"not a sqlite database")
    assert inspect_ledger(p)["classification"] == "UNREADABLE_OR_INVALID_SQLITE"
    link = tmp_path / "linked.db"
    link.symlink_to(p)
    assert inspect_ledger(link)["classification"] == "NOT_REGULAR_EXISTING_FILE"


def test_recording_requires_complete_closed_writer_evidence(tmp_path):
    trial = tmp_path / "trial"
    trial.mkdir()
    session = tmp_path / "session"
    session.mkdir()
    pointer = trial / "recording-session.json"
    pointer.write_text(json.dumps({"session": str(session)}))
    assert inspect_recording_pointer(pointer)["classification"] == "INCOMPLETE_OR_OVERSIZED_RECORDING"
    (session / "research-manifest.json").write_text('{"started_at":"2026-11-01T00:00:00Z"}')
    (session / "research-summary.json").write_text('{"journal":{"closed":true,"writer_alive":false}}')
    result = inspect_recording_pointer(pointer)
    assert result["closed_summary"] is True
    assert result["classification"] == "CLOSED_RECORDING_NOT_ORDER_LEDGER_PROOF"
    assert "session" not in result


def test_bound_rejects_broad_scan(tmp_path):
    with pytest.raises(ValueError, match="CANDIDATE_COUNT_BOUND"):
        inventory([])
    with pytest.raises(ValueError, match="CANDIDATE_COUNT_BOUND"):
        inventory([tmp_path / str(i) for i in range(13)])
