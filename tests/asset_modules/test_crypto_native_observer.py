from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from threading import Event,get_ident
from types import SimpleNamespace
import time

import pytest

from app.asset_modules.admission_controller import PaperAdmissionController
from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.crypto_native_observer import NativeCryptoObserver
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.engine_worker import WorkerState
from app.crypto_research.runtime import CryptoResearchRuntime
from tests.asset_modules.test_crypto_history_adapter import inputs
from tests.asset_modules.test_crypto_worker import NOW
from tests.asset_modules.test_markets import buy


def until(predicate,seconds=8):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('Expected observer transition did not arrive')


@pytest.fixture
def native(tmp_path):
    shared=PaperAdmissionController(tmp_path/'shared.sqlite3')
    shared.configure_account('pool',capital=D(1000),loss_budget=D(50))
    paper=CryptoPaper(tmp_path/'crypto.sqlite3')
    router=CryptoAllocationRouter(shared,paper,account_id='pool',authorize=lambda *args:True)
    runtime=CryptoResearchRuntime(enabled=True,path=tmp_path/'research.jsonl',clock=lambda:NOW)
    bars,q=inputs()
    with runtime._lock:runtime._bar_history.add_many(bars)
    runtime.start()
    assert runtime.admit(q)
    until(lambda:bool(runtime.latest()))
    observer=NativeCryptoObserver(paper,runtime,entry_router=router,clock=lambda:NOW)
    try:yield observer,shared,paper,router,runtime,q
    finally:
        assert observer.close()
        runtime.close();paper.close()


def test_construction_is_inert_and_entry_activation_refused(native):
    observer,shared,paper,router,runtime,q=native
    assert observer.thread is None and observer.protection_thread is None
    assert observer.provider is None and not observer.entries_enabled and not router.enabled
    with pytest.raises(ValueError,match='cannot enable'):observer.entries_enabled=True
    with pytest.raises(RuntimeError,match='own owner thread'):observer.tick()
    assert not paper.snapshot()['positions']
    assert shared.snapshot('pool')['active_commands']==0


def test_actual_subprocess_observes_qualifying_signal_without_entry_or_model(native,monkeypatch):
    from app.asset_modules.grok import GrokClient
    calls=[]
    def forbid(*args,**kwargs):
        calls.append(True);raise AssertionError('No model request permitted')
    monkeypatch.setattr(GrokClient,'request',forbid)
    observer,shared,paper,router,runtime,q=native
    before=runtime.metrics().history_requests_attempted
    observer.start()
    until(lambda:observer.metrics()['qualifying_signals_blocked']==1)
    report=observer.metrics()
    assert report['assessments_submitted']==report['assessments_completed']==1
    assert report['worker_owner_thread']!=get_ident()
    assert observer._native_controller.worker.pid is not None
    assert not calls and report['grok_requests']==0
    assert not paper.snapshot()['positions'] and not paper.snapshot()['events']
    assert shared.snapshot('pool')['active_commands']==0
    assert runtime.metrics().history_requests_attempted==before
    assert not router.enabled
    time.sleep(1.1)
    assert observer.metrics()['assessments_submitted']==1


@pytest.mark.parametrize('case,expected',[
    ('stale','QUOTE_UNAVAILABLE'),('future','QUOTE_UNAVAILABLE'),
    ('gap','HISTORY_NOT_CURRENT_OR_CONTIGUOUS'),
])
def test_bad_cached_inputs_never_reach_worker(native,case,expected):
    observer,shared,paper,router,runtime,q=native
    with runtime._lock:
        if case in {'stale','future'}:
            runtime._latest['BTC/USD']=replace(runtime.latest()[0],
                timestamp=NOW+timedelta(seconds=1) if case=='future' else NOW-timedelta(seconds=3),
                decision_cutoff=NOW+timedelta(seconds=1) if case=='future' else NOW)
        else:
            # Preserve valid bar objects but make the latest series discontinuous.
            from app.crypto_research.evidence import BoundedCryptoBarHistory
            bars=runtime.completed_bars('BTC/USD',as_of=NOW)
            runtime._bar_history=BoundedCryptoBarHistory(maximum_symbols=128,bars_per_series=64)
            runtime._bar_history.add_many((replace(bars[0],opened_at=bars[0].opened_at-timedelta(minutes=1),
                closed_at=bars[0].closed_at-timedelta(minutes=1)),)+bars[1:])
    observer.start()
    until(lambda:observer.metrics()['last_outcomes'].get('BTC/USD')==expected)
    assert observer.metrics()['assessments_submitted']==0
    assert not paper.snapshot()['positions'] and not router.enabled


@pytest.mark.parametrize('pause_settlement',[False,True])
def test_worker_failure_never_stops_independent_protection_or_releases_early(native,monkeypatch,pause_settlement):
    observer,shared,paper,router,runtime,q=native
    router.set_enabled(True)
    assert router.submit({**buy(),'id':'held-before-observer','target':'120'},{'BTC/USD':q},now=NOW)['entry']=='OPENED'
    router.set_enabled(False)
    observer.start()
    until(lambda:observer.metrics()['assessments_completed']>=1)
    process=observer._native_controller.worker._process
    process.terminate();process.join(2)
    until(lambda:observer.metrics()['worker_failures']>0)
    assert observer.protection_thread.is_alive()
    assert paper.snapshot()['positions']  # Existing reservation remains until exit evidence.
    assert shared.snapshot('pool')['active_commands']==1
    closed_locally,allow_settlement=Event(),Event()
    original_reconcile=router.reconcile
    def reconcile_after_local_close():
        # Deterministically expose the real two-transaction window. No lock is
        # held here: native local SELL committed before shared settlement starts.
        if not paper.snapshot()['positions'] and shared.snapshot('pool')['active_commands']:
            closed_locally.set()
            if not allow_settlement.wait(5):
                raise AssertionError('Settlement barrier was not released')
        return original_reconcile()
    if pause_settlement:
        monkeypatch.setattr(router,'reconcile',reconcile_after_local_close)
    try:
        runtime.admit(replace(q,bid=D(130),ask=D('130.1'),price=D(130)))
        if pause_settlement:
            assert closed_locally.wait(5)
            assert not paper.snapshot()['positions']
            assert shared.snapshot('pool')['active_commands']==1
            assert shared.snapshot('pool')['reserved_capital']>0
            allow_settlement.set()
        # A flat local ledger alone is not acknowledgement of shared settlement.
        until(lambda:not paper.snapshot()['positions']
              and shared.snapshot('pool')['active_commands']==0)
        assert shared.snapshot('pool')['reserved_capital']==0
        assert not router.enabled
        until(lambda:not observer.thread.is_alive())  # Failure notification precedes cleanup.
    finally:
        allow_settlement.set()
