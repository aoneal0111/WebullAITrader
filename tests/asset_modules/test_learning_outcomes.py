from copy import deepcopy
import json
import sqlite3

import pytest

from app.asset_modules.grok_learning import GrokLearningLoop, LearningJournal
from app.asset_modules.learning_outcomes import main, read_equity_outcomes


def order(side, quantity, price, *, life="WARRIOR_MOMENTUM_V1|SPY|one", campaign="current", **changes):
    at = "2026-10-13T15:00:00+00:00" if side == "BUY" else "2026-10-13T15:01:00+00:00"
    data = {"paper_campaign_id": campaign, "order_id": side, "status": "FILLED",
            "filled_quantity": quantity, "request": {"symbol": "SPY", "side": side,
            "strategy_lifecycle_id": life, "metadata": {"policy_version": "per-entry-hash"}},
            "fills": [{"fill_id": side, "quantity": quantity, "price": price,
                       "commission": "0.50", "timestamp": at}]}
    data.update(changes)
    return data


def ledger(tmp_path, orders):
    path = tmp_path / "paper.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO metadata VALUES ('active_campaign_id','current')")
        db.execute("CREATE TABLE orders (order_id INTEGER PRIMARY KEY,payload TEXT)")
        for o in orders:
            db.execute("INSERT INTO orders(payload) VALUES (?)", (json.dumps(o),))
        # Event P&L intentionally disagrees with actual fills; do not use it.
        db.execute("CREATE TABLE events (payload TEXT)")
        db.execute("INSERT INTO events VALUES ('{\"realized_pnl\":9999}')")
    return path


def test_fill_cash_fees_active_campaign_and_source_unchanged(tmp_path):
    path = ledger(tmp_path, [order("BUY", "10", "100"), order("SELL", "10", "102"),
                            order("BUY", "10", "100", campaign="old")])
    before = path.read_bytes()
    report = read_equity_outcomes(path)
    assert path.read_bytes() == before
    assert len(report["outcomes"]) == 1
    outcome = report["outcomes"][0]
    assert outcome["net_pnl"] == "19.00"
    assert outcome["recorded_fees"] == "1.00"
    assert outcome["policy_version"] == "UNKNOWN"
    assert outcome["source_kind"] == "UNVERIFIED_PAPER_LEDGER_PROVENANCE"
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    assert journal.ingest_outcomes(report["outcomes"])["inserted"] == 1
    assert LearningJournal(journal.path).ingest_outcomes(report["outcomes"])["already_present"] == 1


@pytest.mark.parametrize("change,reason", [
    ({"status": "WORKING"}, "NONTERMINAL_ORDERS"),
    ({"filled_quantity": "9", "fills": [{"fill_id": "SELL", "quantity": "9", "price": "102",
      "commission": "0.5", "timestamp": "2026-10-13T15:01:00+00:00"}]}, "UNBALANCED_FILLS"),
])
def test_incomplete_trades_excluded(tmp_path, change, reason):
    path = ledger(tmp_path, [order("BUY", "10", "100"), order("SELL", "10", "102", **change)])
    report = read_equity_outcomes(path)
    assert report["outcomes"] == []
    assert report["excluded"] == {reason: 1}


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1e99"])
def test_invalid_amounts_refuse_import(tmp_path, bad):
    path = ledger(tmp_path, [order("BUY", "10", bad), order("SELL", "10", "102")])
    with pytest.raises(ValueError):
        read_equity_outcomes(path)


def test_duplicate_fills_and_quantity_mismatch_refused(tmp_path):
    buy, sell = order("BUY", "10", "100"), order("SELL", "10", "102")
    sell["fills"][0]["fill_id"] = "BUY"
    path = ledger(tmp_path, [buy, sell])
    with pytest.raises(ValueError, match="Duplicate"):
        read_equity_outcomes(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE orders SET payload=? WHERE order_id=2", (json.dumps(order("SELL", "10", "102", filled_quantity="9")),))
    with pytest.raises(ValueError, match="Filled quantity"):
        read_equity_outcomes(path)


def test_policy_and_owner_provenance_not_inferred_from_hash(tmp_path):
    orders = [order("BUY", "10", "100", life="old-hash"), order("SELL", "10", "102", life="old-hash")]
    path = ledger(tmp_path, orders)
    assert read_equity_outcomes(path)["outcomes"][0]["engine"] == "UNKNOWN"
    orders[0]["request"]["metadata"] = {"strategy_owner": "QUICK_SCALPER", "strategy_policy_version": "frozen-scalper-v1"}
    with sqlite3.connect(path) as db:
        db.execute("UPDATE orders SET payload=? WHERE order_id=1", (json.dumps(orders[0]),))
    o = read_equity_outcomes(path)["outcomes"][0]
    assert o["engine"] == "QUICK_SCALPER"
    assert o["policy_version"] == "frozen-scalper-v1"


def test_closed_outcome_conflict_rolls_back_entire_batch(tmp_path):
    source = ledger(tmp_path, [order("BUY", "10", "100"), order("SELL", "10", "102")])
    outcomes = read_equity_outcomes(source)["outcomes"]
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    journal.ingest_outcomes(outcomes)
    extra = deepcopy(outcomes[0]); extra["lifecycle"] = "QUICK_SCALPER|SPY|new"
    conflict = deepcopy(outcomes[0]); conflict["net_pnl"] = "100"
    with pytest.raises(ValueError, match="changed"):
        journal.ingest_outcomes([extra, conflict])
    assert journal.outcome_feedback()["groups"][0]["closed_lifecycles"] == 1


def test_observational_feedback_never_creates_paired_experiments(tmp_path):
    source = ledger(tmp_path, [order("BUY", "10", "100"), order("SELL", "10", "102")])
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    loop = GrokLearningLoop(journal, outcome_source=lambda: read_equity_outcomes(source)["outcomes"])
    assert loop.tick()["state"] == "IDLE"
    assert loop.tick()["state"] == "IDLE"
    assert journal.outcome_feedback()["groups"][0]["net_pnl"] == "19.00"
    with sqlite3.connect(journal.path) as db:
        assert db.execute("SELECT COUNT(*) FROM learning_experiments_v1").fetchone()[0] == 0


def test_missing_source_not_created_and_cli_is_offline(tmp_path, capsys):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        read_equity_outcomes(missing)
    assert not missing.exists()
    source = ledger(tmp_path, [order("BUY", "10", "100"), order("SELL", "10", "102")])
    assert main(["--ledger", str(source), "--journal", str(tmp_path / "learning.sqlite3")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["network_requests"] == report["orders"] == 0


def test_owner_conflict_excluded(tmp_path):
    buy = order("BUY", "10", "100")
    buy["request"]["metadata"]["strategy_owner"] = "QUICK_SCALPER"
    report = read_equity_outcomes(ledger(tmp_path, [buy, order("SELL", "10", "102")]))
    assert report["outcomes"] == []
    assert report["excluded"] == {"OWNERSHIP_CONFLICT": 1}


def test_reentry_under_same_identity_not_counted_as_one_episode(tmp_path):
    orders = [order("BUY", "10", "100"), order("SELL", "10", "102")]
    for side in ("BUY", "SELL"):
        extra = order(side, "10", "100")
        extra["order_id"] += "2"
        extra["fills"][0]["fill_id"] += "2"
        extra["fills"][0]["timestamp"] = "2026-10-13T15:02:00+00:00" if side == "BUY" else "2026-10-13T15:03:00+00:00"
        orders.append(extra)
    report = read_equity_outcomes(ledger(tmp_path, orders))
    assert report["outcomes"] == []
    assert report["excluded"] == {"AMBIGUOUS_INVENTORY_SEQUENCE": 1}


def test_grok_review_retains_exact_feedback_snapshot(tmp_path):
    source = ledger(tmp_path, [order("BUY", "10", "100"), order("SELL", "10", "102")])
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    outcomes = read_equity_outcomes(source)["outcomes"]
    journal.ingest_outcomes(outcomes)
    data = {"version": "ENGINE_COMPARISON_V1", "experiment_id": "paired-v1",
            "dataset_id": "frozen", "cost_model_id": "declared-net", "evidence_kind": "QUOTE_PROXY",
            "engines": {"WARRIOR_MOMENTUM_V1": {"champion": "a", "policies": ["a", "b"]}},
            "results": [dict(engine="WARRIOR_MOMENTUM_V1", policy=p, episode_id="one",
                instrument="SPY", trading_date="2026-10-09", status="CLOSED", net_pnl=net)
                for p, net in [("a", "1"), ("b", "2")]]}
    journal.ingest(data)
    class Client:
        def request(self, role, instruction, context, schema):
            assert context["observational_outcome_feedback"]["groups"][0]["net_pnl"] == "19.00"
            return {"candidate_ids": [], "reason": "Need future evidence."}
    result = journal.review("paired-v1", client=Client())
    extra = deepcopy(outcomes[0]); extra["lifecycle"] += "-new"
    journal.ingest_outcomes([extra])
    assert journal.outcome_feedback()["groups"][0]["closed_lifecycles"] == 2
    assert journal.review("paired-v1")["outcome_feedback"] == result["outcome_feedback"]
    assert result["outcome_feedback"]["groups"][0]["closed_lifecycles"] == 1


def test_bad_source_prevents_review_tick(tmp_path):
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    def failed_source():
        raise ValueError("Reconciliation failed")
    with pytest.raises(ValueError, match="Reconciliation"):
        GrokLearningLoop(journal, outcome_source=failed_source).tick()
