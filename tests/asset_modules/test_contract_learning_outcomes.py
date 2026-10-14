from copy import deepcopy
from datetime import timedelta
import json
import sqlite3

import pytest

from app.asset_modules.contract_learning_outcomes import (
    main, read_contract_outcomes, validate_proxy_outcome,
)
from app.asset_modules.grok_learning import LearningJournal
from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.futures_paper import FuturesPaper
from tests.asset_modules.test_contract_logic import make_plan, quote as option_quote, NOW as OPTION_NOW
from tests.asset_modules.test_futures_paper import prepared, quote as futures_quote, NOW as FUTURES_NOW
from tests.asset_modules.test_grok_learning import experiment, Client


def closed_ledger(tmp_path, engine="OPTIONS", side="LONG", *, close=True):
    path = tmp_path / (engine + side + ".sqlite3")
    if engine == "OPTIONS":
        paper, p, q, now = OptionsPaper(path), make_plan(), option_quote(), OPTION_NOW
        later = now + timedelta(seconds=1)
        exit_quote = option_quote(bid="1.11", ask="1.12", at=later)
    else:
        paper, p, q, now = FuturesPaper(path), prepared(side), futures_quote(), FUTURES_NOW
        later = now + timedelta(seconds=1)
        exit_quote = futures_quote(bid="5003.25" if side=="LONG" else "4996.75",
                                  ask="5003.50" if side=="LONG" else "4997", at=later)
    try:
        paper.set_enabled(True)
        assert paper.enter("one", p, q, now=now) == "OPENED"
        if close:
            assert paper.protect({exit_quote.symbol:exit_quote}, now=later) == {"one":"TARGET"}
    finally:
        paper.close()
    return path


@pytest.mark.parametrize("engine,side,pnl", [
    ("OPTIONS","LONG","26.80"), ("FUTURES","LONG","21"), ("FUTURES","SHORT","21"),
])
def test_native_closed_simulations_are_read_only_and_separate(tmp_path, engine, side, pnl):
    path = closed_ledger(tmp_path, engine, side)
    before = path.read_bytes()
    report = read_contract_outcomes(path, engine)
    assert path.read_bytes() == before
    assert len(report["outcomes"]) == 1 and report["excluded"] == {}
    o = report["outcomes"][0]
    assert o["net_pnl"] == pnl
    assert o["policy_version"] == o["strategy_family"] == "UNKNOWN"
    assert o["source_kind"] == "UNVERIFIED_SIMULATOR_INPUT_PROVENANCE"
    assert "NOT_EXECUTABLE_FILL" in o["evidence_kind"]
    if engine == "FUTURES":
        assert "NOT_INDEPENDENT_MULTIPLIER" in o["reconciliation_scope"]
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    with pytest.raises(ValueError):
        journal.ingest_outcomes([o])  # Never relabel as equity.
    assert journal.ingest_proxy_outcomes([o]) == {"inserted":1, "already_present":0}
    assert LearningJournal(journal.path).ingest_proxy_outcomes([o]) == {"inserted":0, "already_present":1}
    assert journal.outcome_feedback()["groups"] == []
    assert journal.proxy_outcome_feedback()["groups"][0]["net_pnl"] == pnl
    assert journal.review_next()["state"] == "IDLE"


@pytest.mark.parametrize("engine", ["OPTIONS","FUTURES"])
def test_open_position_is_excluded_not_an_outcome(tmp_path, engine):
    report = read_contract_outcomes(closed_ledger(tmp_path, engine, close=False), engine)
    assert report["outcomes"] == [] and report["excluded"] == {"OPEN_INTENTS":1}


@pytest.mark.parametrize("change", ["pnl", "time", "quantity", "symbol", "evidence", "duplicate", "no_settlement", "cash"])
def test_inconsistent_source_refuses_entire_import(tmp_path, change):
    path = closed_ledger(tmp_path)
    with sqlite3.connect(path) as db:
        if change == "pnl":
            db.execute("UPDATE settlements SET net_pnl='100'")
        elif change == "time":
            db.execute("UPDATE settlements SET settled_at='2026-01-01T00:00:00+00:00'")
        elif change == "no_settlement":
            db.execute("DELETE FROM settlements")
        else:
            at, raw = db.execute("SELECT at,payload FROM journal ORDER BY id DESC LIMIT 1").fetchone()
            e = json.loads(raw)
            if change == "quantity": e["quantity"] = 3
            elif change == "symbol": e["symbol"] = "OTHER"
            elif change == "evidence": e["evidence"] = "ACTUAL_BROKER_FILL"
            elif change == "cash": e["fill"] = "1.20"
            if change == "duplicate":
                db.execute("INSERT INTO journal(at,payload) VALUES (?,?)", (at, raw))
            else:
                db.execute("UPDATE journal SET payload=? WHERE id=(SELECT MAX(id) FROM journal)",
                           (json.dumps(e),))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        read_contract_outcomes(path, "OPTIONS")
    assert path.read_bytes() == before


def test_conflicting_reimport_rolls_back_batch_and_keeps_ledger_groups(tmp_path):
    o = read_contract_outcomes(closed_ledger(tmp_path), "OPTIONS")["outcomes"][0]
    j = LearningJournal(tmp_path / "learning.sqlite3")
    j.ingest_proxy_outcomes([o])
    extra = deepcopy(o); extra["ledger_id"] += "-other"
    conflict = deepcopy(o); conflict["net_pnl"] = "100"
    with pytest.raises(ValueError, match="changed"):
        j.ingest_proxy_outcomes([extra, conflict])
    assert len(j.proxy_outcome_feedback()["groups"]) == 1
    j.ingest_proxy_outcomes([extra])
    assert len(j.proxy_outcome_feedback()["groups"]) == 2
    assert j.outcome_feedback()["groups"] == []


def test_old_journal_upgrades_and_review_freezes_separate_simulation_feedback(tmp_path):
    path = tmp_path / "learning.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE learning_outcomes_v1(identity TEXT PRIMARY KEY,payload TEXT NOT NULL)")
    j = LearningJournal(path)
    o = read_contract_outcomes(closed_ledger(tmp_path), "OPTIONS")["outcomes"][0]
    j.ingest_proxy_outcomes([o])
    j.ingest(experiment())
    client = Client()
    review = j.review("trial-v1", client=client)
    context = client.calls[0][1]
    assert context["observational_outcome_feedback"]["groups"] == []
    assert context["quote_simulation_feedback"]["groups"][0]["closed_intents"] == 1
    extra = deepcopy(o); extra["intent_id"] = "second"
    j.ingest_proxy_outcomes([extra])
    assert j.proxy_outcome_feedback()["groups"][0]["closed_intents"] == 2
    assert j.review("trial-v1")["proxy_outcome_feedback"] == review["proxy_outcome_feedback"]
    assert len(client.calls) == 1 and review["promotion"] == "NONE"


def test_missing_wrong_engine_and_same_source_target_are_preserved(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        read_contract_outcomes(missing, "OPTIONS")
    assert not missing.exists()
    path = closed_ledger(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Dedicated"):
        read_contract_outcomes(path, "FUTURES")
    with pytest.raises(SystemExit):
        main(["--engine","OPTIONS","--ledger",str(path),"--journal",str(path)])
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("net_pnl","NaN"), ("quantity","0"), ("recorded_fees","-1"),
    ("policy_version","invented-v1"), ("source_kind","ACTUAL_MARKET_DATA"),
])
def test_unverified_or_invalid_outcomes_rejected(tmp_path, field, value):
    o = read_contract_outcomes(closed_ledger(tmp_path), "OPTIONS")["outcomes"][0]
    o[field] = value
    with pytest.raises(ValueError):
        validate_proxy_outcome(o)


def test_cli_never_calls_model_or_places_orders(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XAI_API_KEY", "do-not-call")
    source = closed_ledger(tmp_path)
    assert main(["--engine","OPTIONS","--ledger",str(source),
                 "--journal",str(tmp_path / "learning.sqlite3")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["network_requests"] == result["orders"] == 0
    assert result["promotion"] == "NONE"
