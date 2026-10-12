from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from app.asset_modules.admission_controller import PaperAdmissionController
from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.supervisor import CryptoSupervisor
from tests.asset_modules.test_supervisor import market, proposal
from tests.asset_modules.test_futures_allocation import equity


@pytest.fixture
def system(tmp_path):
    shared = PaperAdmissionController(tmp_path/'shared.db')
    shared.configure_account('pool', capital=D(1000), loss_budget=D(50))
    ledger = CryptoPaper(tmp_path/'crypto.db')
    router = CryptoAllocationRouter(shared, ledger, account_id='pool', authorize=lambda *a: True)
    row = market()
    supervisor = CryptoSupervisor(ledger, lambda: [row],
        SimpleNamespace(propose=lambda context: [proposal()]), entry_router=router)
    try:
        yield shared, ledger, router, supervisor, row
    finally:
        assert supervisor.close()
        ledger.close()


def test_default_off_and_shared_equity_competition(system):
    shared, ledger, router, supervisor, row = system
    supervisor.tick()
    assert supervisor.request_count == 0 and not router.enabled
    now = datetime.now(UTC)
    request = replace(equity(capital=D(800)), quote_at=now, expires_at=now+timedelta(seconds=30))
    assert shared.reserve(request, now=now).state == 'RESERVED'
    supervisor.entries_enabled = True
    supervisor.tick()
    assert supervisor.status == 'SHARED_CAPITAL_LIMIT'
    assert not ledger.snapshot()['positions']
    shared.abandon_unsubmitted('equity', EngineId.WARRIOR)
    supervisor.next_request = 0
    row.timestamp = datetime.now(UTC)
    supervisor.tick()
    assert supervisor.status == 'PAPER FILLED'
    assert shared.snapshot('pool')['reserved_capital'] > D(200)
    assert shared.snapshot('pool')['active_commands'] == 1


def test_router_owner_and_activation_are_explicit(system, tmp_path):
    _, ledger, router, supervisor, _ = system
    with pytest.raises(ValueError): supervisor.entries_enabled = 'false'
    other = CryptoPaper(tmp_path/'other.db')
    try:
        with pytest.raises(ValueError, match='own'): CryptoSupervisor(other, lambda: [], entry_router=router)
    finally: other.close()
    router.set_enabled(True)
    another = CryptoSupervisor(ledger, lambda: [], entry_router=router)
    assert not router.enabled and not another.entries_enabled


def test_revocation_during_model_call_disables_router(system):
    shared, ledger, router, supervisor, _ = system
    def propose(context):
        supervisor.entries_enabled = False
        return [proposal()]
    supervisor.provider = SimpleNamespace(propose=propose)
    supervisor.entries_enabled = True
    supervisor.tick()
    assert not router.enabled and not ledger.snapshot()['positions']
    assert shared.snapshot('pool')['active_commands'] == 0


def test_post_model_quote_uses_shared_two_second_freshness(system):
    shared, ledger, _, supervisor, row = system
    def propose(context):
        row.timestamp = datetime.now(UTC)-timedelta(seconds=3)
        return [proposal()]
    supervisor.provider = SimpleNamespace(propose=propose)
    supervisor.entries_enabled = True
    supervisor.tick()
    assert not ledger.snapshot()['positions']
    assert shared.snapshot('pool')['active_commands'] == 0
    assert supervisor.status != 'PAPER FILLED'


def test_disabled_ai_still_closes_and_settles_once(system):
    shared, ledger, router, supervisor, row = system
    supervisor.entries_enabled = True
    supervisor.tick()
    supervisor.entries_enabled = False
    row.bid, row.ask, row.timestamp = D(110), D('110.1'), datetime.now(UTC)
    supervisor.tick()
    assert not ledger.snapshot()['positions'] and not router.enabled
    assert shared.snapshot('pool')['active_commands'] == 0
    pnl = shared.allocation_balance('pool')['crypto_realized_pnl']
    assert pnl > 0
    supervisor.tick()
    assert shared.allocation_balance('pool')['crypto_realized_pnl'] == pnl


def test_ai_sell_settles_without_entry_token(system):
    shared, ledger, _, supervisor, row = system
    supervisor.entries_enabled = True
    supervisor.tick()
    row.timestamp = datetime.now(UTC)
    supervisor.provider = SimpleNamespace(propose=lambda context: [dict(symbol='BTC/USD', action='SELL')])
    supervisor.next_request = 0
    supervisor.tick()
    assert supervisor.status == 'PAPER FILLED'
    assert not ledger.snapshot()['positions']
    assert shared.snapshot('pool')['active_commands'] == 0


def test_unknown_dispatch_error_keeps_reservation_and_blocks_retry(system, monkeypatch):
    shared, ledger, router, supervisor, row = system
    original = ledger.apply
    def broken(*a, **kw): raise ValueError('uncertain write')
    monkeypatch.setattr(ledger, 'apply', broken)
    supervisor.entries_enabled = True
    supervisor.tick()
    assert supervisor.status == 'ALLOCATION REVIEW REQUIRED: ValueError'
    assert shared.snapshot('pool')['active_commands'] == 1
    monkeypatch.setattr(ledger, 'apply', original)
    row.timestamp = datetime.now(UTC)
    supervisor.next_request = 0
    supervisor.tick()
    assert supervisor.status == 'ALLOCATION_RECONCILIATION_REQUIRED'
    assert 'UNCERTAIN' in supervisor.protection_status
    assert not ledger.snapshot()['positions']
    assert shared.snapshot('pool')['active_commands'] == 1


def test_protection_and_shared_settlement_continue_while_ai_blocked(system):
    shared, ledger, _, supervisor, row = system
    supervisor.entries_enabled = True
    supervisor.tick()
    entered, release = Event(), Event()
    def propose(context):
        entered.set()
        release.wait(5)
        return [proposal()]
    supervisor.provider = SimpleNamespace(propose=propose)
    supervisor.next_request = 0
    supervisor.start()
    try:
        assert entered.wait(2)
        row.bid, row.ask, row.timestamp = D(110), D('110.1'), datetime.now(UTC)
        deadline = monotonic()+3
        while shared.snapshot('pool')['active_commands'] and monotonic() < deadline:
            sleep(.02)
        assert not ledger.snapshot()['positions']
        assert shared.snapshot('pool')['active_commands'] == 0
        assert not release.is_set()
        supervisor.entries_enabled = False
    finally:
        release.set()
        assert supervisor.close()
