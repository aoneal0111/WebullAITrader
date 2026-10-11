"""Owned AI entries retain canonical acknowledgements and deterministic exits."""
from dataclasses import replace
from decimal import Decimal as D
import json

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_paper_entries import GrokPaperEntries, PreparedEntry
from tests.asset_modules.test_controlled_paper_service import setup, fill, placement
from tests.asset_modules.test_grok_paper_entries import prepared, suite, run


@pytest.mark.parametrize("engine", [EngineId.WARRIOR, EngineId.SCALPER])
def test_native_placement_factory_binds_owned_economics_without_rewriting(setup, engine):
    commands, _, _, _, _, _ = setup
    original = prepared(commands, engine).placement
    entry = PreparedEntry.from_placement(original, evidence={"setup": "triggered", "placement": {"quantity": "999"}})
    assert entry.placement is original
    assert entry.candidate.engine is engine
    assert entry.candidate.lifecycle_id == original.order.strategy_lifecycle_id
    assert json.loads(entry.candidate.evidence_json)["placement"]["quantity"] == "10"
    assert PreparedEntry.from_placement(original, evidence={}).candidate.candidate_id == entry.candidate.candidate_id


@pytest.mark.parametrize("metadata", [
    {"strategy_owner": None}, {"strategy_owner": "CRYPTO"}, {"structural_stop": "0"},
    {"structural_stop": "6"}, {"structural_stop": "NaN"}, {"risk_dollars": "0"},
    {"risk_dollars": "NaN"}, {"opportunity_id": ""},
])
def test_direct_prepared_entry_cannot_omit_owner_or_valid_economics(setup, metadata):
    commands, _, _, _, _, _ = setup
    entry = prepared(commands)
    bad = placement(commands, **{"strategy_owner": "WARRIOR_MOMENTUM", **metadata})
    with pytest.raises(ValueError):
        PreparedEntry(entry.candidate, bad)
    assert not commands.order_book.history()


@pytest.mark.parametrize("engine", [EngineId.WARRIOR, EngineId.SCALPER])
def test_acknowledgement_installs_bridge_owner_and_existing_stop_path_works(setup, engine):
    commands, bridge, service, account, quote, clock = setup
    ai, _ = suite()
    entry = PreparedEntry.from_placement(prepared(commands, engine).placement, evidence={"setup": "triggered"})
    dispatcher = GrokPaperEntries(ai, service, authorize=lambda _: True, clock=lambda: clock[0],
        on_acknowledged=lambda e, r: bridge.adopt_entry_acknowledgement(e.placement, r))
    report = run(dispatcher, [entry])
    outcome = report.outcomes[0]
    assert outcome.state == "SUBMITTED" and outcome.result.success
    identity = entry.candidate.lifecycle_id
    assert bridge._active_by_symbol["SPY"] == identity
    assert bridge._entry_orders[identity] == outcome.result.broker_order_id
    assert bridge.adopt_entry_acknowledgement(entry.placement, outcome.result)
    fill(commands)
    # Protection is the native bridge's existing deterministic path, not AI.
    quote[0] = None
    account[0] = replace(account[0], risk_engine_approved=False)
    stop = bridge.ensure_exit("SPY", 10, D("5.9"), "STOP", identity)
    assert stop.protection_active
    working = commands.order_book.open_orders_for_symbol("SPY")
    assert len(working) == 1 and working[0].request.execution_reason == "STOP"
    assert working[0].request.metadata["strategy_owner"] == entry.placement.order.metadata["strategy_owner"]
    fill(commands, bid="5.89", ask="5.9", sequence=2)
    assert bridge._authoritative_quantity("SPY") == 0
    assert ai.client.requests == 2


@pytest.mark.parametrize("failure", ["false", "exception"])
def test_handoff_failure_preserves_acknowledgement_and_never_retries(setup, failure):
    commands, _, service, _, _, clock = setup
    ai, _ = suite()
    def callback(entry, result):
        if failure == "exception":
            raise RuntimeError("private callback detail")
        return False
    report = run(GrokPaperEntries(ai, service, authorize=lambda _: True,
        clock=lambda: clock[0], on_acknowledged=callback), [prepared(commands)])
    outcome = report.outcomes[0]
    assert outcome.state == "ACKNOWLEDGED_HANDOFF_INCOMPLETE"
    assert outcome.result.success and commands.order_book.contains(outcome.result.broker_order_id)
    assert len(commands.order_book.history()) == 1
    assert "private" not in str(report)


@pytest.mark.parametrize("change", ["unknown_id", "client", "request", "account", "session", "price", "owner"])
def test_adoption_refuses_forged_or_unrelated_acknowledgement(setup, change):
    commands, bridge, service, _, _, _ = setup
    entry = prepared(commands)
    result = service.place_order(entry.placement)
    p = entry.placement
    if change == "unknown_id":
        result = replace(result, broker_order_id="PAPER-UNKNOWN")
    elif change in {"client", "request"}:
        result = replace(result, **{change + "_order_id" if change == "client" else "request_id": "other"})
    elif change == "session":
        p = replace(p, session_id="other")
    else:
        fields = {"account_id": "other"} if change == "account" else {"limit_price": D("6.01")} if change == "price" else {
            "metadata": {**dict(p.order.metadata), "strategy_owner": "QUICK_SCALPER"}}
        p = replace(p, order=replace(p.order, **fields))
    assert not bridge.adopt_entry_acknowledgement(p, result)
    assert not bridge._active_by_symbol and not bridge._entry_orders
    assert len(commands.order_book.history()) == 1


def test_acknowledged_order_still_adopted_after_entries_disabled(setup):
    commands, bridge, service, _, _, _ = setup
    entry = prepared(commands)
    result = service.place_order(entry.placement)
    bridge.enabled = False
    assert bridge.adopt_entry_acknowledgement(entry.placement, result)
    assert bridge._entry_orders[entry.candidate.lifecycle_id] == result.broker_order_id


def test_adoption_cannot_overwrite_different_symbol_owner(setup):
    commands, bridge, service, _, _, _ = setup
    entry = prepared(commands)
    result = service.place_order(entry.placement)
    bridge._active_by_symbol["SPY"] = "existing-owner"
    assert not bridge.adopt_entry_acknowledgement(entry.placement, result)
    assert bridge._active_by_symbol["SPY"] == "existing-owner"


def test_terminal_unfilled_acknowledgement_does_not_restore_active_position(setup):
    commands, bridge, service, _, _, _ = setup
    entry = prepared(commands)
    result = service.place_order(entry.placement)
    commands.order_book.cancel(result.broker_order_id, at=commands.order_book.get(result.broker_order_id).created_at)
    assert bridge.adopt_entry_acknowledgement(entry.placement, result)
    assert not bridge._active_by_symbol


def test_old_acknowledgement_cannot_adopt_unrelated_inventory(setup):
    commands, bridge, service, _, _, _ = setup
    entry = prepared(commands)
    result = service.place_order(entry.placement)
    commands.order_book.cancel(result.broker_order_id)
    other = placement(commands, strategy_owner="WARRIOR_MOMENTUM")
    other = replace(other, order=replace(other.order, strategy_lifecycle_id="WARRIOR_MOMENTUM_V1|SPY|other"))
    assert service.service.place_order(other).success
    fill(commands)
    assert bridge._authoritative_quantity("SPY") == 10
    assert bridge.adopt_entry_acknowledgement(entry.placement, result)
    assert not bridge._active_by_symbol
