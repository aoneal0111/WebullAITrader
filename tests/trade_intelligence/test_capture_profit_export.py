from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from app.trade_intelligence.knowledge.capture_profit_export import (
    bounded_rows, export_batch, main, read_database,
)
from tests.trade_intelligence.test_capture_profit_audit import sample, stamp


@pytest.fixture
def root(tmp_path):
    (tmp_path / "data/sandbox").mkdir(parents=True)
    (tmp_path / "data/warrior_momentum_v1_forward").mkdir(parents=True)
    data = sample()
    with sqlite3.connect(tmp_path / "data/sandbox/paper-execution.sqlite3") as db:
        db.executescript("CREATE TABLE metadata(key,value); CREATE TABLE orders(payload);")
        db.execute("INSERT INTO metadata VALUES ('active_campaign_id','campaign')")
        for o in data["orders"]:
            o["created_at"] = stamp(0)
            o["status"] = "FILLED"
            db.execute("INSERT INTO orders VALUES (?)", (json.dumps(o),))
    with sqlite3.connect(tmp_path / "data/warrior_momentum_v1_forward/forward_capture.sqlite3") as db:
        db.execute("CREATE TABLE capture_records(sequence INTEGER PRIMARY KEY,timestamp,symbol,record_type,payload_json)")
        for r in data["capture_records"]:
            db.execute("INSERT INTO capture_records(timestamp,symbol,record_type,payload_json) VALUES (?,?,?,?)",
                       (r["timestamp"],r["symbol"],"EXECUTION_PRICE_PATH",json.dumps(r["payload"])))
        db.execute("INSERT INTO capture_records(timestamp,symbol,record_type,payload_json) VALUES (?,?,?,?)",
                   (stamp(0),"TEST","DECISION",json.dumps({"setup":{"state":"FORMING"}})))
    return tmp_path


def test_export_reconciles_and_keeps_context_unproven(root):
    source_paths = list((root / "data").rglob("*.sqlite3"))
    originals = {p:p.read_bytes() for p in source_paths}
    manifest, archive = export_batch(root, stamp(0))
    assert manifest["eligible_closed"] == 1
    item = manifest["exports"][0]
    assert item["audit"]["lifecycles"][0]["actual_closed_pnl"] == -10
    assert item["entry_context_records"] == 1
    with zipfile.ZipFile(archive) as z:
        data = json.loads(z.read(item["file"]))
        assert data["entry_context"]["scope"] == "NEARBY_SYMBOL_TIME_RECORDS_NOT_PROVEN_ORDER_LINKAGE"
        assert data["entry_context"]["records"][0]["payload"]["setup"]["state"] == "FORMING"
        assert "manifest.json" in z.namelist()
    assert all(p.read_bytes() == originals[p] for p in source_paths)


def test_offset_and_ordering_are_entry_time_not_pnl(root):
    order_path = root / "data/sandbox/paper-execution.sqlite3"
    capture_path = root / "data/warrior_momentum_v1_forward/forward_capture.sqlite3"
    with sqlite3.connect(order_path) as db:
        originals = [json.loads(r[0]) for r in db.execute("SELECT payload FROM orders")]
        for o in originals:
            o["order_id"] += "-later"
            o["request"]["strategy_lifecycle_id"] += "-later"
            o["created_at"] = stamp(1)
            db.execute("INSERT INTO orders VALUES (?)",(json.dumps(o),))
    with sqlite3.connect(capture_path) as db:
        rows = db.execute("SELECT timestamp,symbol,record_type,payload_json FROM capture_records WHERE record_type='EXECUTION_PRICE_PATH'").fetchall()
        for t,s,typ,raw in rows:
            p=json.loads(raw);p["lifecycle_id"] += "-later"
            if "order_id" in p:p["order_id"] += "-later"
            db.execute("INSERT INTO capture_records(timestamp,symbol,record_type,payload_json) VALUES (?,?,?,?)",(t,s,typ,json.dumps(p)))
    m,_ = export_batch(root,stamp(0),offset=1,limit=1)
    assert m["eligible_closed"] == 2
    assert m["exports"][0]["file"] == "batch_2_capture.json"
    assert m["exports"][0]["lifecycle"].endswith("-later")


def test_open_orders_and_other_campaign_are_excluded(root):
    with sqlite3.connect(root / "data/sandbox/paper-execution.sqlite3") as db:
        rows=[json.loads(r[0]) for r in db.execute("SELECT payload FROM orders")]
        db.execute("DELETE FROM orders")
        for o in rows:
            o["status"] = "WORKING"
            db.execute("INSERT INTO orders VALUES (?)",(json.dumps(o),))
            other=deepcopy(o);other["paper_campaign_id"]="other";other["status"]="FILLED"
            db.execute("INSERT INTO orders VALUES (?)",(json.dumps(other),))
    m,_=export_batch(root,stamp(0))
    assert m["eligible_closed"] == 0
    assert m["exports"] == []


def test_timezone_equivalent_start_selects_same_trade(root):
    m,_=export_batch(root,"2026-10-09T10:00:00-05:00")
    assert m["eligible_closed"] == 1


def test_missing_capture_reported_not_silently_dropped(root):
    with sqlite3.connect(root / "data/warrior_momentum_v1_forward/forward_capture.sqlite3") as db:
        db.execute("DELETE FROM capture_records WHERE record_type='EXECUTION_PRICE_PATH'")
    m,_=export_batch(root,stamp(0))
    assert m["excluded"][0]["reason"] == "NO_CAPTURE_RECORDS"
    snapshot=root/"snapshot.json";snapshot.write_text(json.dumps({"runtime_started_at":stamp(0)}))
    assert main([str(snapshot),"--root",str(root)]) == 1


def test_context_overflow_excludes_instead_of_truncating(root):
    with sqlite3.connect(root / "data/warrior_momentum_v1_forward/forward_capture.sqlite3") as db:
        db.executemany("INSERT INTO capture_records(timestamp,symbol,record_type,payload_json) VALUES (?,?,?,?)",
                       [(stamp(0),"TEST","DECISION","{}")]*1000)
    m,_=export_batch(root,stamp(0))
    assert m["exports"] == []
    assert m["excluded"][0]["reason"] == "DATABASE_RESULT_LIMIT"


@pytest.mark.parametrize("options", [{"offset":-1},{"limit":0},{"limit":6}])
def test_batch_bounds(root,options):
    with pytest.raises(ValueError,match="INVALID_BATCH_BOUNDS"):
        export_batch(root,stamp(0),**options)


def test_read_only_and_streamed_limits(root):
    with read_database(root / "data/sandbox/paper-execution.sqlite3") as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM orders")
        with pytest.raises(ValueError,match="DATABASE_RESULT_LIMIT"):
            bounded_rows(db,"SELECT payload FROM orders",(),1,32_000_000)
        with pytest.raises(ValueError,match="DATABASE_RESULT_LIMIT"):
            bounded_rows(db,"SELECT payload FROM orders",(),5000,1)
