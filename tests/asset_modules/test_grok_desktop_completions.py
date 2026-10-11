"""Background entry acknowledgements survive polling and desktop shutdown."""
from dataclasses import replace
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_paper_entries import EntryDispatch, EntryOutcome
from app.asset_modules.grok_runtime_handoff import GrokRuntimeHandoff, RuntimeEntry
from app.strategies.warrior_momentum import WarriorCaptureHealth, WarriorDesktopSidecar
from tests.asset_modules.test_controlled_paper_service import setup, fill, NOW
from tests.asset_modules.test_grok_paper_entries import prepared, suite
from tests.asset_modules.test_grok_runtime_handoff import warrior, signal, AUTH, FloatProvenance, scalper


def bound(commands, engine=EngineId.WARRIOR, symbol="SPY"):
    p = prepared(commands, engine, symbol)
    if engine is EngineId.WARRIOR:
        return RuntimeEntry(p, replace(signal(), symbol=symbol), FloatProvenance.UNKNOWN, AUTH)
    return RuntimeEntry(p)


def coordinator(setup, transform=None, authorize=lambda _: True):
    commands, bridge, service, _, _, clock = setup
    ai, _ = suite(transform)
    return GrokRuntimeHandoff(ai, service, bridge, authorize=authorize, clock=lambda: clock[0]), ai


def submit(handoff, entries):
    return handoff.submit(entries, engine_evidence={}, account_evidence={})


def finished(handoff):
    handoff.worker._thread.join(2)
    assert not handoff.worker._thread.is_alive()


@pytest.mark.parametrize("engine", [EngineId.WARRIOR, EngineId.SCALPER])
def test_poll_adopts_on_owner_lane_once_without_another_buy(setup, warrior, engine):
    commands, _, _, _, _, _ = setup
    handoff, ai = coordinator(setup)
    runtime = scalper(setup)
    entry = bound(commands, engine)
    assert ai.client.requests == 0
    assert submit(handoff, [entry]) == "ACCEPTED"
    finished(handoff)
    fill(commands)
    # Protect filled canonical inventory even before polling management state.
    handoff.reconcile_protection()
    assert any(o.request.execution_reason == "STOP" for o in commands.order_book.open_orders_for_symbol("SPY"))
    assert handoff.drain(warrior, runtime) == (entry.prepared.candidate.candidate_id,)
    assert handoff.drain(warrior, runtime) == ()
    assert len([o for o in commands.order_book.history() if o.request.side.value == "BUY"]) == 1
    assert handoff.snapshot()["pending_entries"] == 0
    assert ai.client.requests == 2
    assert handoff.close(warrior, runtime)


def test_background_ack_does_not_call_warrior_runtime_until_poll(setup):
    commands, *_ = setup
    handoff, _ = coordinator(setup)
    calls = []
    runtime = SimpleNamespace(adopt_acknowledged_entry=lambda *a, **k: calls.append(get_ident()) or True)
    assert submit(handoff, [bound(commands)]) == "ACCEPTED"
    finished(handoff)
    assert not calls
    handoff.drain(runtime, None)
    assert calls == [get_ident()]
    assert handoff.close(runtime, None)


def test_failed_adoption_is_retained_and_blocks_new_batches(setup, warrior):
    commands, *_ = setup
    handoff, ai = coordinator(setup)
    entry = bound(commands)
    assert submit(handoff, [entry]) == "ACCEPTED"
    finished(handoff)
    assert not handoff.drain(None, None)
    assert handoff.snapshot()["pending_acknowledgements"] == 1
    assert submit(handoff, [bound(commands, EngineId.SCALPER, "QQQ")]) == "BUSY"
    assert handoff.drain(warrior, None)
    assert ai.client.requests == 2
    assert handoff.close(warrior, None)


def test_terminal_unfilled_ack_is_resolved_without_reviving_state(setup, warrior):
    commands, *_ = setup
    handoff, _ = coordinator(setup)
    assert submit(handoff, [bound(commands)]) == "ACCEPTED"
    finished(handoff)
    order = next(o for o in commands.order_book.history() if o.request.side.value == "BUY")
    commands.order_book.cancel(order.order_id)
    assert handoff.drain(warrior, None)
    assert not warrior.open_paper_symbols
    assert handoff.close(warrior, None)


@pytest.mark.parametrize("role", ["MASTER", EngineId.WARRIOR.value])
def test_abstention_releases_batch_without_adopting(setup, warrior, role):
    commands, *_ = setup
    def abstain(context, answer):
        if context["role"] == role:
            answer["selected_ids"] = []
    handoff, _ = coordinator(setup, abstain)
    assert submit(handoff, [bound(commands)]) == "ACCEPTED"
    finished(handoff)
    assert not handoff.drain(warrior, None)
    assert not commands.order_book.history()
    assert handoff.snapshot()["pending_entries"] == 0
    assert handoff.close(warrior, None)


@pytest.mark.parametrize("failure", ["DISPATCH_UNCERTAIN", "ENTRY_WORKER_UNAVAILABLE", "UNKNOWN_ACK"])
def test_uncertainty_is_latched_and_cannot_be_retried_as_an_entry(setup, warrior, failure):
    commands, *_ = setup
    handoff, _ = coordinator(setup)
    entry = bound(commands)
    handoff._entries[entry.prepared.candidate.candidate_id] = entry
    outcomes = (() if failure == "ENTRY_WORKER_UNAVAILABLE" else
        (EntryOutcome("unknown" if failure == "UNKNOWN_ACK" else entry.prepared.candidate.candidate_id, failure),))
    handoff.worker._report = EntryDispatch("PARTIAL", failure, outcomes)
    assert not handoff.drain(warrior, None)
    assert handoff.snapshot()["reconciliation_required"]
    assert submit(handoff, [entry]) == "BUSY"
    assert not handoff.close(warrior, None)
    assert submit(handoff, [entry]) == "CLOSED"
    assert not commands.order_book.history()


def test_stop_during_ai_does_not_report_false_completion(setup, warrior):
    commands, *_ = setup
    entered, release = Event(), Event()
    def block(context, answer):
        entered.set()
        assert release.wait(3)
    handoff, _ = coordinator(setup, block)
    try:
        assert submit(handoff, [bound(commands)]) == "ACCEPTED"
        assert entered.wait(1)
        assert not handoff.drain(warrior, None)
        assert not handoff.close(warrior, None)
        release.set()
        finished(handoff)
        assert handoff.close(warrior, None)
        assert not commands.order_book.history()
    finally:
        release.set()
        handoff.worker.close(1)


@pytest.mark.parametrize("change", ["symbol", "stop", "trigger", "lifecycle", "authorization", "missing"])
def test_original_warrior_context_must_match_native_placement(setup, change):
    commands, *_ = setup
    entry = bound(commands)
    values = {"symbol": {"symbol": "QQQ"}, "stop": {"stop_price": signal().stop_price - 1},
              "trigger": {"entry_trigger": signal().entry_trigger + 1},
              "lifecycle": {"structural_episode_id": "other"}}
    with pytest.raises(ValueError):
        if change in values:
            replace(entry, signal=replace(signal(), **values[change]))
        elif change == "authorization":
            replace(entry, symbol_authorization=replace(AUTH, authorized=False))
        else:
            RuntimeEntry(entry.prepared)


def test_duplicate_candidate_and_lifecycle_rejected_before_review(setup):
    commands, *_ = setup
    handoff, ai = coordinator(setup)
    entry = bound(commands)
    assert submit(handoff, [entry, entry]) == "INVALID"
    assert ai.client.requests == 0
    assert handoff.close(None, None)


def desktop(tmp_path, handoff, runtime):
    sidecar = WarriorDesktopSidecar(enabled=True, storage_path=tmp_path / "unused.db", clock=lambda: NOW)
    sidecar.bind_grok_runtime_handoff(handoff)
    sidecar._service = runtime
    sidecar._health = WarriorCaptureHealth.RUNNING
    sidecar._accept_execution = True
    sidecar._consume = lambda event: None
    return sidecar


def test_desktop_polls_on_processing_lane_and_preserves_shutdown_context(setup, warrior, tmp_path):
    commands, *_ = setup
    handoff, _ = coordinator(setup)
    sidecar = desktop(tmp_path, handoff, warrior)
    assert sidecar.submit_grok_entries([bound(commands)], engine_evidence={}, account_evidence={}) == "ACCEPTED"
    finished(handoff)
    assert sidecar.poll_grok_completions()
    assert warrior.open_paper_symbols == ("SPY",)
    sidecar.stop()
    assert handoff.snapshot()["closed"]
    assert sidecar.submit_grok_entries([bound(commands)], engine_evidence={}, account_evidence={}) == "NOT_READY"


def test_desktop_timeout_retains_runtime_until_ack_can_be_adopted(setup, warrior, tmp_path):
    commands, *_ = setup
    entered, release = Event(), Event()
    def block(context, answer):
        entered.set()
        assert release.wait(3)
    handoff, _ = coordinator(setup, block)
    sidecar = desktop(tmp_path, handoff, warrior)
    try:
        assert sidecar.submit_grok_entries([bound(commands)], engine_evidence={}, account_evidence={}) == "ACCEPTED"
        assert entered.wait(1)
        with pytest.raises(TimeoutError, match="Grok entry"):
            sidecar.stop()
        assert sidecar._service is warrior
        assert not sidecar._accept_execution
        release.set()
        finished(handoff)
        sidecar.stop()
        assert not commands.order_book.history()
    finally:
        release.set()
        handoff.worker.close(1)


def test_uncertain_dispatch_accepts_only_canonical_recovered_ack(setup, warrior):
    commands, *_ = setup
    handoff, _ = coordinator(setup)
    entry = bound(commands)
    assert submit(handoff, [entry]) == "ACCEPTED"
    finished(handoff)
    report = handoff.worker._report
    result = report.outcomes[0].result
    key = entry.prepared.candidate.candidate_id
    handoff.worker._report = EntryDispatch("PARTIAL", "test", (EntryOutcome(key, "DISPATCH_UNCERTAIN"),))
    handoff.drain(warrior, None)
    assert handoff.snapshot()["reconciliation_required"]
    assert not handoff.recover_acknowledgement(key, replace(result, broker_order_id="PAPER-UNKNOWN"))
    assert not handoff.recover_acknowledgement("other", result)
    assert handoff.recover_acknowledgement(key, result)
    assert handoff.drain(warrior, None) == (key,)
    assert not handoff.snapshot()["reconciliation_required"]
    assert handoff.close(warrior, None)
    assert len([o for o in commands.order_book.history() if o.request.side.value == "BUY"]) == 1


def test_gateway_callback_protection_is_reentrant_and_precedes_completion_poll(setup, warrior, tmp_path):
    commands, *_ = setup
    handoff, _ = coordinator(setup)
    sidecar = desktop(tmp_path, handoff, warrior)
    original = commands.gateway._event_sink
    def sink(event):
        if original is not None:
            original(event)
        sidecar.observe_paper_event(event)
    commands.gateway._event_sink = sink
    try:
        assert submit(handoff, [bound(commands)]) == "ACCEPTED"
        finished(handoff)
        fill(commands)
        assert not warrior.open_paper_symbols
        stops = [o for o in commands.order_book.open_orders_for_symbol("SPY") if o.request.execution_reason == "STOP"]
        assert len(stops) == 1 and stops[0].request.quantity == 10
        assert sidecar.poll_grok_completions()
        assert handoff.close(warrior, None)
    finally:
        commands.gateway._event_sink = original
        handoff.worker.close(1)


def test_stop_racing_order_submission_preserves_late_ack(setup, warrior, tmp_path):
    commands, _, service, *_ = setup
    handoff, _ = coordinator(setup)
    sidecar = desktop(tmp_path, handoff, warrior)
    entered, release = Event(), Event()
    place = service.place_order
    def delayed(placement):
        entered.set()
        assert release.wait(3)
        return place(placement)
    service.place_order = delayed
    try:
        assert submit(handoff, [bound(commands)]) == "ACCEPTED"
        assert entered.wait(1)
        with pytest.raises(TimeoutError, match="Grok entry"):
            sidecar.stop()
        assert sidecar._service is warrior
        release.set()
        finished(handoff)
        sidecar.stop()  # A late acknowledgement is adopted even after entry permission is off.
        assert warrior.open_paper_symbols == ("SPY",)
        assert handoff.snapshot()["pending_entries"] == 0
        assert len([o for o in commands.order_book.history() if o.request.side.value == "BUY"]) == 1
    finally:
        release.set()
        handoff.worker.close(1)
        service.place_order = place


def test_only_quiescent_reconciled_handoff_can_be_replaced(setup, warrior, tmp_path):
    commands, *_ = setup
    old, _ = coordinator(setup)
    new, _ = coordinator(setup)
    sidecar = desktop(tmp_path, old, warrior)
    with pytest.raises(ValueError, match="requires reconciliation"):
        sidecar.bind_grok_runtime_handoff(new)
    assert old.close(warrior, None)
    sidecar.bind_grok_runtime_handoff(new)
    assert sidecar.grok_entry_status() == new.snapshot()
    assert new.close(warrior, None)


@pytest.mark.parametrize("change", ["service", "environment"])
def test_handoff_refuses_mismatched_bridge_before_review(setup, change):
    _, bridge, service, _, _, clock = setup
    ai, _ = suite()
    if change == "service":
        bridge.trading_service = object()
    else:
        bridge.mode = "LIVE"
    with pytest.raises(TypeError, match="Canonical bridge"):
        GrokRuntimeHandoff(ai, service, bridge, authorize=lambda _: True, clock=lambda: clock[0])
    assert ai.client.requests == 0
