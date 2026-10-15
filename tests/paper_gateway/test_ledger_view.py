from datetime import timedelta
import json
import sqlite3
import subprocess
import sys

import pytest

from app.gui.runtime_ownership import acquire_runtime_ownership
from app.market_data.models import MarketEvent, MarketEventType, ResumePayload
from app.paper_trading.ledger_view import PaperLedgerReader
from app.paper_trading.protection_host import PaperProtectionHost
from app.paper_trading.protection_host_check import T0, seed, quote


def test_reads_existing_orders_without_mutation_while_protection_host_owns_ledger(tmp_path):
    path = tmp_path / "paper-execution.sqlite3"
    seed(path)
    now = [T0 + timedelta(seconds=2)]
    host = PaperProtectionHost(path, environment="PAPER", clock=lambda: now[0])
    try:
        before = path.read_bytes()
        reader = PaperLedgerReader(path)
        saved = reader.read()
        assert saved.status == "AVAILABLE" and saved.report()["saved_working_orders"] == 1
        assert path.read_bytes() == before
        lease_path = path.with_name("atlas-paper-runtime.lock")
        assert acquire_runtime_ownership(lease_path) is None
        now[0] += timedelta(seconds=1)
        host.process(MarketEvent(2,now[0],"DEMO","test",MarketEventType.RESUME,ResumePayload("test")))
        now[0] += timedelta(seconds=1)
        assert host.process(quote(now[0])) == "PROCESSED"
        latest = reader.read()
        assert latest.status == "AVAILABLE" and latest.report()["saved_working_orders"] == 0
        net = sum(float(o.filled_quantity)*(1 if o.side == "BUY" else -1) for o in latest.orders.orders)
        assert net == 0 and saved.report()["saved_working_orders"] == 1
        assert acquire_runtime_ownership(lease_path) is None
    finally:
        host.close()


def test_read_only_sql_connections_never_initialize_source_or_lock_file(tmp_path, monkeypatch):
    path = tmp_path / "paper.sqlite3"
    seed(path)
    original = sqlite3.connect
    queries = []
    def connect(database, **kwargs):
        assert database.endswith("?mode=ro") and kwargs["uri"] is True
        connection = original(database, **kwargs)
        connection.set_trace_callback(queries.append)
        return connection
    monkeypatch.setattr("app.paper_trading.ledger_view.sqlite3.connect", connect)
    before = path.read_bytes()
    assert PaperLedgerReader(path).read().status == "AVAILABLE"
    assert path.read_bytes() == before
    assert not path.with_name("atlas-paper-runtime.lock").exists()
    assert all(q.split()[0] in {"SELECT","PRAGMA","BEGIN"} for q in queries)


def test_missing_file_is_unknown_and_is_not_created(tmp_path):
    path = tmp_path / "absent" / "paper.sqlite3"
    result = PaperLedgerReader(path).read()
    assert result.status == "NOT_FOUND" and result.report()["saved_orders"] is None
    assert not path.parent.exists()


def test_busy_source_has_unknown_counts_not_zero_and_retry_recovers(tmp_path):
    path = tmp_path / "paper.sqlite3"
    seed(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("BEGIN EXCLUSIVE")
        result = PaperLedgerReader(path).read()
        assert result.status == "BUSY" and result.report()["saved_working_orders"] is None
        assert not result.orders.orders
        db.rollback()
    assert PaperLedgerReader(path).read().status == "AVAILABLE"


@pytest.mark.parametrize("change", ["identity","quantity","fill","timestamp","owner","asset","metadata","size"])
def test_invalid_saved_records_never_become_empty_authoritative_positions(tmp_path, change):
    path = tmp_path / "paper.sqlite3"
    seed(path)
    with sqlite3.connect(path) as db:
        identity, raw = db.execute("SELECT order_id,payload FROM orders WHERE json_extract(payload,'$.request.side')='BUY'").fetchone()
        value = json.loads(raw)
        if change == "identity": value["order_id"] = "other"
        if change == "quantity": value["filled_quantity"] = "NaN"
        if change == "fill": value["fills"][0]["quantity"] = "1"
        if change == "timestamp": value["updated_at"] = "2026-10-14T15:00:00"
        if change == "owner": value["request"]["metadata"]["strategy_owner"] = "QUICK_SCALPER"
        if change == "asset": value["request"]["asset_class"] = "CRYPTO"
        if change == "metadata": value["request"]["metadata"] = ["bad"]
        if change == "size": value["padding"] = "x"*131073
        db.execute("UPDATE orders SET payload=? WHERE order_id=?", (json.dumps(value),identity))
    result = PaperLedgerReader(path).read()
    assert result.status == "INVALID"
    assert not result.orders.orders and result.report()["saved_orders"] is None


def test_active_campaign_filter_excludes_other_campaign_orders(tmp_path):
    path = tmp_path / "paper.sqlite3"
    seed(path)
    before = PaperLedgerReader(path).read()
    with sqlite3.connect(path) as db:
        raw = db.execute("SELECT payload FROM orders LIMIT 1").fetchone()[0]
        value = json.loads(raw)
        value["order_id"], value["paper_campaign_id"] = "other", "other-campaign"
        db.execute("INSERT INTO orders VALUES(?,?)", ("other",json.dumps(value)))
    after = PaperLedgerReader(path).read()
    assert after.status == "AVAILABLE" and after.orders == before.orders


@pytest.mark.parametrize("field,value", [("environment","LIVE"),("account_id","other"),("schema_version","2")])
def test_wrong_ledger_identity_is_rejected_read_only(tmp_path, field, value):
    path = tmp_path / "paper.sqlite3"
    seed(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE metadata SET value=? WHERE key=?", (value,field))
    before = path.read_bytes()
    assert PaperLedgerReader(path).read().status == "INVALID"
    assert path.read_bytes() == before


def test_check_command_reads_without_qt_or_trading_composition(tmp_path):
    path = tmp_path / "paper.sqlite3"
    seed(path)
    code = ("import sys; from app.gui.paper_viewer import main; "
            "result=main(['--ledger',sys.argv[1],'--check']); "
            "assert 'PySide6.QtWidgets' not in sys.modules; "
            "assert 'app.composition.desktop' not in sys.modules; raise SystemExit(result)")
    result = subprocess.run([sys.executable,"-c",code,str(path)],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "AVAILABLE" and report["runtime_health"] == "UNKNOWN"
    assert report["orders_submitted"] == 0 and report["network_requests"] == 0


def test_viewer_reuses_strategy_pages_clears_failed_reads_and_does_not_own_runtime(tmp_path):
    from PySide6.QtWidgets import QApplication
    from app.gui.paper_viewer import create_window
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "paper.sqlite3"
    seed(path, lifecycle="QUICK_SCALPER|DEMO|VIEW")
    lease = acquire_runtime_ownership(path.with_name("atlas-paper-runtime.lock"))
    window = create_window(PaperLedgerReader(path))
    try:
        assert window.tabs.count() == 3
        assert window.pages[0].inventory.rowCount() == 0
        assert window.pages[1].inventory.item(0,2).text() == "10"
        path.unlink()
        window.refresh()
        assert "NOT_FOUND" in window.notice.text()
        assert window.pages[1].inventory.rowCount() == 0
        assert window.all_orders._table.rowCount() == 0
        assert acquire_runtime_ownership(path.with_name("atlas-paper-runtime.lock")) is None
    finally:
        window.close()
        lease.release()
