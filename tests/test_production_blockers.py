from __future__ import annotations

import sqlite3
import threading

import pytest

from app.authorization import AuthorizationRegistry, consume
from app.live_execution.events import ExecutionEventLog
from app.live_execution.models import LocalPortfolioState
from app.live_execution.order_manager import submit
from app.live_execution.order_translation import translate_order
from app.live_execution.recovery import DurableExecutionJournal, MutationState, reconcile_startup
from app.live_execution.webull_adapter import WebullAdapter
from test_live_execution import NOW, MockBroker, authorize


def test_restart_replay_is_rejected(tmp_path):
    path = tmp_path / "authorization.sqlite3"
    validated, registry = authorize(registry=AuthorizationRegistry(path))
    consume(registry, validated.intent, validated.authorization, NOW)
    registry.close()
    recovered = AuthorizationRegistry(path)
    with pytest.raises(ValueError, match="consumed"):
        consume(recovered, validated.intent, validated.authorization, NOW)


def test_concurrent_consume_has_exactly_one_winner(tmp_path):
    path = tmp_path / "authorization.sqlite3"
    validated, first = authorize(registry=AuthorizationRegistry(path))
    second = AuthorizationRegistry(path)
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(registry):
        barrier.wait()
        try:
            consume(registry, validated.intent, validated.authorization, NOW)
            outcomes.append("SUCCESS")
        except ValueError:
            outcomes.append("REJECTED")

    threads = (threading.Thread(target=worker, args=(first,)), threading.Thread(target=worker, args=(second,)))
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(outcomes) == ["REJECTED", "SUCCESS"]


def test_duplicate_identity_and_transaction_rollback(tmp_path):
    path = tmp_path / "authorization.sqlite3"
    validated, registry = authorize(registry=AuthorizationRegistry(path))
    with pytest.raises(ValueError, match="identity"):
        authorize(registry=registry)
    registry._connection.execute("CREATE TRIGGER reject_consumption BEFORE UPDATE OF consumed_at ON authorizations BEGIN SELECT RAISE(ABORT,'forced rollback'); END")
    with pytest.raises(sqlite3.IntegrityError):
        consume(registry, validated.intent, validated.authorization, NOW)
    assert validated.authorization.authorization_id not in registry.consumed_ids


def test_crash_before_dispatch_recovers_once(tmp_path):
    validated, registry = authorize(registry=AuthorizationRegistry(tmp_path / "authorization.sqlite3"))
    request = translate_order(validated, NOW)
    journal = DurableExecutionJournal(tmp_path / "execution.sqlite3")
    journal.prepare("SUBMIT:req-1", "SUBMIT", "req-1", validated.authorization.authorization_id, request, NOW)
    consume(registry, validated.intent, validated.authorization, NOW)
    broker = MockBroker()
    recovered = reconcile_startup(DurableExecutionJournal(journal.database_path), broker, registry, NOW)
    assert recovered[0].state is MutationState.ACKNOWLEDGED
    assert len(broker.submissions) == 1
    assert reconcile_startup(DurableExecutionJournal(journal.database_path), broker, registry, NOW) == ()
    assert len(broker.submissions) == 1


def test_crash_after_dispatch_reconciles_without_resubmission(tmp_path):
    validated, registry = authorize(registry=AuthorizationRegistry(tmp_path / "authorization.sqlite3"))
    request = translate_order(validated, NOW)
    journal = DurableExecutionJournal(tmp_path / "execution.sqlite3")
    journal.prepare("SUBMIT:req-1", "SUBMIT", "req-1", validated.authorization.authorization_id, request, NOW)
    consume(registry, validated.intent, validated.authorization, NOW)
    journal.transition("SUBMIT:req-1", MutationState.PREPARED, MutationState.AUTHORIZED, NOW)
    journal.transition("SUBMIT:req-1", MutationState.AUTHORIZED, MutationState.DISPATCHING, NOW)
    broker = MockBroker(); broker.submit_order(request); broker.orders = tuple([broker.submit_order(request)])
    broker.submissions.clear()
    recovered = reconcile_startup(DurableExecutionJournal(journal.database_path), broker, registry, NOW)
    assert recovered[0].state is MutationState.ACKNOWLEDGED
    assert broker.submissions == []


def test_ambiguous_dispatch_fails_closed_and_replay_is_idempotent(tmp_path):
    validated, registry = authorize(registry=AuthorizationRegistry(tmp_path / "authorization.sqlite3"))
    request = translate_order(validated, NOW); journal = DurableExecutionJournal(tmp_path / "execution.sqlite3")
    journal.prepare("SUBMIT:req-1", "SUBMIT", "req-1", validated.authorization.authorization_id, request, NOW)
    consume(registry, validated.intent, validated.authorization, NOW)
    journal.transition("SUBMIT:req-1", MutationState.PREPARED, MutationState.AUTHORIZED, NOW)
    journal.transition("SUBMIT:req-1", MutationState.AUTHORIZED, MutationState.DISPATCHING, NOW)
    broker = MockBroker()
    assert reconcile_startup(journal, broker, registry, NOW)[0].state is MutationState.UNRESOLVED
    assert reconcile_startup(journal, broker, registry, NOW)[0].state is MutationState.UNRESOLVED
    assert broker.submissions == []


def test_normal_submit_records_acknowledged_mutation(tmp_path):
    validated, registry = authorize(registry=AuthorizationRegistry(tmp_path / "authorization.sqlite3"))
    journal = DurableExecutionJournal(tmp_path / "execution.sqlite3"); broker = MockBroker()
    submit(validated, broker, LocalPortfolioState(), ExecutionEventLog(), NOW, registry, journal)
    assert journal.get("SUBMIT:req-1").state is MutationState.ACKNOWLEDGED


def test_capability_bound_live_adapter_requires_durable_journal(tmp_path):
    class CapabilityTransport(MockBroker):
        def bind_mutation_capability(self, capability): self.capability = capability
        def dispatch_submit(self, capability, request):
            assert capability is self.capability
            return super().submit_order(request)
        def dispatch_cancel(self, capability, client_order_id): return super().cancel_order(client_order_id)
        def dispatch_replace(self, capability, client_order_id, request): return super().replace_order(client_order_id,request)
    adapter=WebullAdapter(CapabilityTransport());validated,registry=authorize(
        registry=AuthorizationRegistry(tmp_path/"authorization.sqlite3"))
    with pytest.raises(ValueError,match="durable execution journal"):
        submit(validated,adapter,LocalPortfolioState(),ExecutionEventLog(),NOW,registry)
