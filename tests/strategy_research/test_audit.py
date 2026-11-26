"""Integration checks over real SQLite snapshots and canonical reconciliation."""
import json
import socket
import sqlite3

import pytest

from app.strategy_research.audit import main
from tests.asset_modules.test_learning_outcomes import ledger, order


def test_export_keeps_winners_losers_partials_and_exclusions(tmp_path, capsys, monkeypatch):
    buy = order("BUY", "10", "100")
    first = order("SELL", "4", "102")
    final = order("SELL", "6", "102", order_id="SELL-final")
    final["fills"][0]["fill_id"] = "SELL-final"
    life = "QUICK_SCALPER|QQQ|two"
    scalp_buy = order("BUY", "10", "100", life=life, order_id="scalp-buy")
    scalp_sell = order("SELL", "10", "99", life=life, order_id="scalp-sell")
    scalp_buy["fills"][0]["fill_id"] = "scalp-buy"
    scalp_sell["fills"][0]["fill_id"] = "scalp-sell"
    nofill = order("BUY", "0", "100", life="QUICK_SCALPER|SPY|none", fills=[], status="CANCELLED")
    path = ledger(tmp_path, [buy, first, final, scalp_buy, scalp_sell, nofill,
                             order("BUY", "10", "100", campaign="old")])
    before = path.read_bytes()
    before_files = set(tmp_path.iterdir())
    def reject_network(*args, **kwargs):
        raise AssertionError("audit attempted network I/O")
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    assert main(["--ledger", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert path.read_bytes() == before
    assert set(tmp_path.iterdir()) == before_files
    assert report["source"]["excluded"] == {"NO_FILLS": 1}
    assert len(report["source"]["outcomes"]) == 2
    groups = {g["engine"]: g for g in report["summary"]["groups"]}
    warrior = groups["WARRIOR_MOMENTUM_V1"]
    assert warrior["closed_lifecycles"] == 1  # Two profitable partials are one win.
    assert warrior["net_pnl"] == "18.50"
    assert warrior["wins"] == 1
    assert groups["QUICK_SCALPER"]["losses"] == 1
    assert report["execution_authorized"] is False


def test_missing_source_is_not_created(tmp_path, capsys):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(SystemExit) as error:
        main(["--ledger", str(path)])
    assert error.value.code == 2
    assert not path.exists()
    assert "Equity audit failed" in capsys.readouterr().err


def test_corrupt_fill_export_fails_without_partial_json(tmp_path, capsys):
    path = ledger(tmp_path, [order("BUY", "10", "100"), order("SELL", "10", "102")])
    with sqlite3.connect(path) as db:
        raw = json.loads(db.execute("SELECT payload FROM orders WHERE order_id=2").fetchone()[0])
        raw["fills"][0]["fill_id"] = "BUY"
        db.execute("UPDATE orders SET payload=? WHERE order_id=2", (json.dumps(raw),))
    before = path.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(["--ledger", str(path)])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Duplicate durable fill" in captured.err
    assert path.read_bytes() == before
