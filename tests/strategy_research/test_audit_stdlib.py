"""Standalone audit verification: python -m unittest, no pytest conftest."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from app.strategy_research.audit import main
from app.read_only_sqlite import bounded_rows, read_database


def make_ledger(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO metadata VALUES('active_campaign_id','current')")
        db.execute("CREATE TABLE orders(order_id INTEGER PRIMARY KEY, payload TEXT)")
        for identity, side, quantity, price in (("buy", "BUY", "10", "100"),
                                               ("partial", "SELL", "4", "102"),
                                               ("final", "SELL", "6", "99")):
            raw = {"order_id": identity, "paper_campaign_id": "current", "status": "FILLED",
                   "filled_quantity": quantity,
                   "request": {"symbol": "SPY", "side": side,
                               "strategy_lifecycle_id": "WARRIOR_MOMENTUM_V1|SPY|one", "metadata": {}},
                   "fills": [{"fill_id": identity, "quantity": quantity, "price": price,
                              "commission": "0.50", "timestamp": "2026-10-13T15:00:00+00:00" if side == "BUY"
                              else "2026-10-13T15:01:00+00:00"}]}
            db.execute("INSERT INTO orders(payload) VALUES(?)", (json.dumps(raw),))


class StandaloneAuditTests(unittest.TestCase):
    def test_export_reconciles_partial_fills_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ledger.sqlite3"
            make_ledger(path)
            before = path.read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--ledger", str(path)]), 0)
            report = json.loads(output.getvalue())
            group = report["summary"]["groups"][0]
            self.assertEqual(group["closed_lifecycles"], 1)
            self.assertEqual(group["net_pnl"], "0.50")
            self.assertEqual(group["wins"], 1)
            self.assertFalse(report["execution_authorized"])
            self.assertEqual(path.read_bytes(), before)

    def test_missing_source_is_not_created(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "missing.sqlite3"
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["--ledger", str(path)])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(path.exists())

    def test_connection_and_result_bounds_are_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ledger.sqlite3"
            make_ledger(path)
            with read_database(path) as db:
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("DELETE FROM orders")
                with self.assertRaisesRegex(ValueError, "DATABASE_RESULT_LIMIT"):
                    bounded_rows(db, "SELECT payload FROM orders", (), 1, 100000)
                with self.assertRaisesRegex(ValueError, "DATABASE_RESULT_LIMIT"):
                    bounded_rows(db, "SELECT payload FROM orders", (), 10, 1)

    def test_audit_runs_with_scientific_and_runtime_imports_unavailable(self):
        # A fresh process catches accidental imports before they enter the
        # module cache, and exercises a real SQLite export through the CLI.
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ledger.sqlite3"
            make_ledger(path)
            code = """
import importlib.abc, runpy, sys
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ('pandas', 'numpy', 'PySide6', 'webull', 'httpx', 'pytest',
                     'app.trade_intelligence', 'app.strategies', 'app.gui')
        if any(fullname == p or fullname.startswith(p + '.') for p in forbidden):
            raise AssertionError('Unexpected audit dependency: ' + fullname)
sys.meta_path.insert(0, Reject())
sys.argv = ['audit', '--ledger', sys.argv[1]]
runpy.run_module('app.strategy_research.audit', run_name='__main__')
"""
            result = subprocess.run([sys.executable, "-S", "-c", code, str(path)],
                                    cwd=Path(__file__).resolve().parents[2],
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["source"]["campaign"], "current")


if __name__ == "__main__":
    unittest.main()
