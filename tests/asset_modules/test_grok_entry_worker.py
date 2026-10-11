from threading import Event
from time import monotonic, sleep

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_entry_worker import GrokEntryWorker
from app.asset_modules.grok_paper_entries import GrokPaperEntries
from tests.asset_modules.test_controlled_paper_service import setup, placement, fill
from tests.asset_modules.test_grok_paper_entries import prepared, suite


def completed(worker):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        result = worker.poll()
        if result is not None:
            return result
        sleep(.005)
    raise AssertionError("Worker did not complete")


def worker_for(setup, transform=None, authorize=lambda e: True):
    commands, _, service, _, _, clock = setup
    ai, ticks = suite(transform)
    worker = GrokEntryWorker(GrokPaperEntries(ai, service, authorize=authorize, clock=lambda: clock[0]))
    return commands, worker, ai, ticks


def submit(worker, entries, engine=None, account=None):
    return worker.submit(entries, engine_evidence=engine or {}, account_evidence=account or {})


@pytest.mark.parametrize("engine", [EngineId.WARRIOR, EngineId.SCALPER])
def test_worker_dispatches_one_owned_entry_and_retains_completion(setup, engine):
    commands, worker, ai, _ = worker_for(setup)
    try:
        entry = prepared(commands, engine)
        assert ai.client.requests == 0  # Construction never starts AI.
        assert submit(worker, [entry]) == "ACCEPTED"
        report = completed(worker)
        assert report.outcomes[0].state == "SUBMITTED"
        assert commands.order_book.history()[0].request.strategy_lifecycle_id == entry.candidate.lifecycle_id
        assert worker.close(1)
        assert submit(worker, [entry]) == "CLOSED"
        assert worker.poll() is None
    finally:
        worker.close(1)


def test_successful_worker_uses_real_durable_controller(setup):
    commands, worker, ai, _ = worker_for(setup)
    try:
        assert submit(worker, [prepared(commands)]) == "ACCEPTED"
        report = completed(worker)
        assert report.outcomes[0].state == "SUBMITTED"
        assert len(commands.order_book.history()) == 1
        assert ai.client.requests == 2
        assert setup[2].status()["reserved_capital"] == 60
    finally:
        assert worker.close(1)


def test_blocked_ai_refuses_backlog_and_cannot_block_protective_exit(setup):
    commands, _, service, *_ = setup
    assert service.place_order(placement(commands)).success
    fill(commands)
    entered, release = Event(), Event()
    def slow(context, answer):
        entered.set()
        assert release.wait(3)
    _, worker, _, _ = worker_for(setup, slow)
    try:
        entry = prepared(commands, EngineId.SCALPER, "QQQ")
        assert submit(worker, [entry]) == "ACCEPTED"
        assert entered.wait(1)
        assert worker.poll() is None
        assert submit(worker, [entry]) == "BUSY"
        assert service.place_order(placement(commands, side="SELL", reason="STOP")).success
        fill(commands, bid="6", ask="6.01", sequence=2)
        assert sum(o.filled_quantity for o in commands.order_book.history() if o.request.side.value == "SELL") == 10
        assert worker.close() is False  # Do not falsely report shutdown complete.
        release.set()
        assert completed(worker).reason == "ENTRY_WORKER_STOPPED"
        assert not any(o.symbol == "QQQ" for o in commands.order_book.history())
    finally:
        release.set()
        assert worker.close(1)


def test_shutdown_during_master_review_revokes_selected_entry(setup):
    entered, release = Event(), Event()
    def slow(context, answer):
        if context["role"] == "MASTER":
            entered.set()
            assert release.wait(3)
    commands, worker, _, _ = worker_for(setup, slow)
    try:
        assert submit(worker, [prepared(commands)]) == "ACCEPTED"
        assert entered.wait(1)
        assert not worker.close()
        release.set()
        report = completed(worker)
        assert report.outcomes[0].state == "WORKER_STOPPED"
        assert not commands.order_book.history()
    finally:
        release.set()
        assert worker.close(1)


def test_shutdown_inside_final_authorization_is_checked_again(setup):
    holder, calls = {}, [0]
    def authorize(entry):
        calls[0] += 1
        if calls[0] == 2:
            holder["worker"].close()
        return True
    commands, worker, _, _ = worker_for(setup, authorize=authorize)
    holder["worker"] = worker
    try:
        assert submit(worker, [prepared(commands)]) == "ACCEPTED"
        assert completed(worker).outcomes[0].state == "WORKER_STOPPED"
        assert not commands.order_book.history()
    finally:
        assert worker.close(1)


def test_evidence_is_snapshot_and_unread_result_prevents_new_batch(setup):
    entered, release = Event(), Event()
    seen = []
    def capture(context, answer):
        entered.set()
        assert release.wait(3)
        seen.append(context["evidence"])
    commands, worker, _, _ = worker_for(setup, capture)
    account = {"capital": "10000"}
    evidence = {EngineId.WARRIOR.value: {"setup": "confirmed"}}
    entry = prepared(commands)
    try:
        assert submit(worker, [entry], evidence, account) == "ACCEPTED"
        assert entered.wait(1)
        account["capital"] = "0"
        evidence[EngineId.WARRIOR.value]["setup"] = "changed"
        release.set()
        # Joining is only a test barrier; do not consume the report yet.
        worker._thread.join(2)
        assert not worker._thread.is_alive()
        assert submit(worker, [entry]) == "BUSY"
        assert completed(worker).outcomes[0].state == "SUBMITTED"
        assert seen[0]["setup"] == "confirmed"
        assert seen[1]["account"]["capital"] == "10000"
    finally:
        release.set()
        assert worker.close(1)


def test_invalid_inputs_do_not_start_provider_or_orders(setup):
    commands, worker, ai, _ = worker_for(setup)
    try:
        entry = prepared(commands)
        assert submit(worker, []) == "INVALID"
        assert submit(worker, [entry] * 33) == "INVALID"
        assert submit(worker, [object()]) == "INVALID"
        assert submit(worker, [entry], account={"value": float("nan")}) == "INVALID"
        assert submit(worker, [entry], account={"value": "x" * 65536}) == "INVALID"
        assert submit(worker, [entry], account=[1]) == "INVALID"
        assert ai.client.requests == 0
        assert not commands.order_book.history()
    finally:
        assert worker.close(1)


def test_provider_failure_never_falls_back_and_report_remains_available(setup):
    def fail(context, answer):
        raise RuntimeError("private-key-must-not-leak")
    commands, worker, _, _ = worker_for(setup, fail)
    try:
        assert submit(worker, [prepared(commands)]) == "ACCEPTED"
        report = completed(worker)
        assert report.reason == "AI_ENTRY_REVIEW_UNAVAILABLE"
        assert "private" not in repr(report)
        assert not commands.order_book.history()
    finally:
        assert worker.close(1)


def test_shutdown_timeout_is_bounded(setup):
    _, worker, _, _ = worker_for(setup)
    for value in (-1, 2, float("nan"), "1"):
        with pytest.raises(ValueError):
            worker.close(value)
    assert worker.close()
