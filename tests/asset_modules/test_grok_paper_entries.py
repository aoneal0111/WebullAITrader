from dataclasses import replace
from datetime import timedelta
import json
from threading import Event, Thread

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok import GrokClient
from app.asset_modules.grok_coordinator import Candidate, GrokSuite, IntentKind
from app.asset_modules.grok_paper_entries import GrokPaperEntries, PreparedEntry
from tests.asset_modules.test_controlled_paper_service import setup, placement, fill, NOW


def prepared(commands, engine=EngineId.WARRIOR, symbol="SPY"):
    p = placement(commands, engine=engine.value, symbol=symbol,
                  strategy_owner="WARRIOR_MOMENTUM" if engine is EngineId.WARRIOR else "QUICK_SCALPER")
    c = Candidate(engine, engine.value + "|" + symbol, NOW + timedelta(seconds=60),
                  '{"setup":"confirmed","source":"test"}', lifecycle_id=p.order.strategy_lifecycle_id)
    return PreparedEntry(c, p)


def suite(transform=None):
    ticks = [10]
    def transport(request):
        context = json.loads(json.loads(request.data)["input"][1]["content"])
        answer = {"request_id": context["request_id"],
                  "selected_ids": [c["candidate_id"] for c in context["candidates"]][:2], "reason": "test"}
        if transform:
            transform(context, answer)
        return json.dumps({"status": "completed", "output": [{"type": "message", "role": "assistant",
            "status": "completed", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]}).encode()
    result = GrokSuite(GrokClient("test", "grok-test", transport=transport, clock=lambda: ticks[0]), clock=lambda: NOW)
    return result, ticks


def run(dispatcher, entries):
    return dispatcher.review_and_submit(entries, engine_evidence={}, account_evidence={"capital": "10000"})


def test_both_engine_and_master_selection_reach_durable_paper_controller(setup):
    commands, bridge, service, account, quote, clock = setup
    ai, _ = suite()
    dispatcher = GrokPaperEntries(ai, service, authorize=lambda e: True, clock=lambda: clock[0])
    entries = [prepared(commands), prepared(commands, EngineId.SCALPER, "QQQ")]
    report = run(dispatcher, entries)
    assert [o.state for o in report.outcomes] == ["SUBMITTED", "SUBMITTED"]
    assert {o.request.strategy_lifecycle_id for o in commands.order_book.history()} == {
        e.candidate.lifecycle_id for e in entries}
    assert service.status()["reserved_capital"] == 120
    assert ai.client.requests == 3


@pytest.mark.parametrize("role", ["MASTER", EngineId.WARRIOR.value])
def test_either_engine_or_master_can_abstain_without_an_order(setup, role):
    commands, _, service, _, _, clock = setup
    def abstain(context, answer):
        if context["role"] == role:
            answer["selected_ids"] = []
    ai, _ = suite(abstain)
    report = run(GrokPaperEntries(ai, service, authorize=lambda e: True, clock=lambda: clock[0]),
                 [prepared(commands)])
    assert report.reason == "MASTER_ABSTAINED" and not report.outcomes
    assert not commands.order_book.history()


@pytest.mark.parametrize("revocation", ["setup", "quote", "risk", "expiry"])
def test_changes_during_model_review_are_rechecked_before_submission(setup, revocation):
    commands, _, service, account, quote, clock = setup
    valid = [True]
    def revoke(context, answer):
        if context["role"] != "MASTER":
            return
        if revocation == "setup":
            valid[0] = False
        elif revocation == "quote":
            quote[0] = replace(quote[0], ask=quote[0].ask + 1)
        elif revocation == "risk":
            account[0] = replace(account[0], risk_engine_approved=False)
        else:
            clock[0] = NOW + timedelta(seconds=61)
    ai, _ = suite(revoke)
    report = run(GrokPaperEntries(ai, service, authorize=lambda e: valid[0], clock=lambda: clock[0]),
                 [prepared(commands)])
    assert not commands.order_book.history()
    assert not any(o.state == "SUBMITTED" for o in report.outcomes)
    assert service.status()["active_commands"] == 0


def test_revoked_initial_setup_makes_no_model_call(setup):
    commands, _, service, _, _, clock = setup
    ai, _ = suite()
    report = run(GrokPaperEntries(ai, service, authorize=lambda e: None, clock=lambda: clock[0]),
                 [prepared(commands)])
    assert report.reason == "STRATEGY_NOT_AUTHORIZED"
    assert ai.client.requests == 0


def test_repeated_review_cannot_submit_a_second_lifecycle_entry(setup):
    commands, _, service, _, _, clock = setup
    ai, ticks = suite()
    dispatcher = GrokPaperEntries(ai, service, authorize=lambda e: True, clock=lambda: clock[0])
    entries = [prepared(commands)]
    assert run(dispatcher, entries).outcomes[0].state == "SUBMITTED"
    ticks[0] += 61
    assert run(dispatcher, entries).outcomes[0].state == "CONTROLLER_REFUSED"
    assert len(commands.order_book.history()) == 1


def test_model_error_does_not_fall_back_to_direct_submission(setup):
    commands, _, service, _, _, clock = setup
    def fail(context, answer):
        raise RuntimeError("private provider failure")
    ai, _ = suite(fail)
    report = run(GrokPaperEntries(ai, service, authorize=lambda e: True, clock=lambda: clock[0]),
                 [prepared(commands)])
    assert report.reason == "AI_ENTRY_REVIEW_UNAVAILABLE"
    assert "private" not in repr(report)
    assert not commands.order_book.history()


def test_master_cannot_dispatch_an_unbound_recommendation_from_another_loop(setup):
    commands, _, service, _, _, clock = setup
    ai, _ = suite()
    foreign = Candidate(EngineId.CRYPTO, "CRYPTO|foreign", NOW + timedelta(seconds=60), '{}')
    ai.review(EngineId.CRYPTO, [foreign], evidence={})
    report = run(GrokPaperEntries(ai, service, authorize=lambda e: True, clock=lambda: clock[0]),
                 [prepared(commands)])
    assert report.reason == "MASTER_BINDING_CONFLICT"
    assert not commands.order_book.history()


def test_authorization_exception_after_one_dispatch_preserves_submitted_outcome(setup):
    commands, _, service, _, _, clock = setup
    ai, _ = suite()
    calls = [0]
    def authorize(entry):
        calls[0] += 1
        if calls[0] == 4:
            raise RuntimeError("setup unavailable")
        return True
    dispatcher = GrokPaperEntries(ai, service, authorize=authorize, clock=lambda: clock[0])
    report = run(dispatcher, [prepared(commands), prepared(commands, EngineId.SCALPER, "QQQ")])
    assert report.state == "PARTIAL"
    assert len(report.outcomes) == 1 and report.outcomes[0].state == "SUBMITTED"
    assert len(commands.order_book.history()) == 1


def test_conflicting_account_or_duplicate_batch_fails_before_model_call(setup):
    commands, _, service, _, _, clock = setup
    ai, _ = suite()
    dispatcher = GrokPaperEntries(ai, service, authorize=lambda e: True, clock=lambda: clock[0])
    entry = prepared(commands)
    bad = replace(entry, placement=replace(entry.placement, order=replace(entry.placement.order, account_id="other")))
    for entries in ([bad], [entry, entry]):
        assert run(dispatcher, entries).state == "NOT_SENT"
    assert ai.client.requests == 0 and not commands.order_book.history()


def test_management_and_foreign_ownership_are_not_entry_routes(setup):
    commands, *_ = setup
    entry = prepared(commands)
    for c in (replace(entry.candidate, engine=EngineId.SCALPER),
              replace(entry.candidate, intent=IntentKind.CLOSE, expected_revision=0)):
        with pytest.raises(ValueError):
            PreparedEntry(c, entry.placement)


def test_slow_model_and_busy_dispatcher_cannot_block_existing_stop(setup):
    commands, _, service, _, _, clock = setup
    assert service.place_order(placement(commands)).success
    fill(commands)
    entered, release = Event(), Event()
    authorized = [True]
    def slow(context, answer):
        entered.set()
        assert release.wait(2)
    ai, _ = suite(slow)
    dispatcher = GrokPaperEntries(ai, service, authorize=lambda e: authorized[0], clock=lambda: clock[0])
    entry = prepared(commands, EngineId.SCALPER, "QQQ")
    reports = []
    worker = Thread(target=lambda: reports.append(run(dispatcher, [entry])))
    try:
        worker.start()
        assert entered.wait(1)
        assert run(dispatcher, [entry]).reason == "AI_ENTRY_WORKER_BUSY"
        assert service.place_order(placement(commands, side="SELL", reason="STOP")).success
        fill(commands, bid="6", ask="6.01", sequence=2)
        assert sum(o.filled_quantity for o in commands.order_book.history() if o.request.side.value == "SELL") == 10
        authorized[0] = False
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert not any(o.state == "SUBMITTED" for o in reports[0].outcomes)
