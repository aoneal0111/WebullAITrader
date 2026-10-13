from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
import time

import pytest

from app.asset_modules.admission_controller import PaperAdmissionController
from app.asset_modules.contract_logic import Bar
from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.crypto_signals import CryptoHistory, CryptoSignalSettings, propose
from app.asset_modules.crypto_worker import CryptoSignalWorker, CryptoWorkerController, _pack, _assess, _quote
from app.asset_modules.engine_worker import WorkerState
from app.crypto_research.models import CryptoPair

NOW = datetime(2026,10,13,15,tzinfo=UTC)
SETTINGS = CryptoSignalSettings()


def data():
    bars = tuple(Bar(NOW-timedelta(seconds=1, minutes=21-i), D('100.2'),D(98),D(100)) for i in range(21))
    bars += (Bar(NOW-timedelta(seconds=1),D('101.2'),D(100),D(101)),)
    row = SimpleNamespace(pair=CryptoPair('BTC','USD','BTCUSD'), bid=D(101),ask=D('101.1'),timestamp=NOW)
    return CryptoHistory('BTC/USD',bars),row


def wait(worker):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        reply=worker.poll()
        if reply:return reply
        if worker.state==WorkerState.FAILED:raise AssertionError(worker.status())
        time.sleep(.01)
    raise AssertionError('No worker reply')


@pytest.fixture
def worker():
    w=CryptoSignalWorker(timeout_seconds=10)
    w.start()
    assert wait(w)['kind']=='READY'
    try:yield w
    finally:w.close()


@pytest.fixture
def system(tmp_path, worker):
    shared=PaperAdmissionController(tmp_path/'shared.db')
    shared.configure_account('pool',capital=D(1000),loss_budget=D(50))
    ledger=CryptoPaper(tmp_path/'crypto.db')
    router=CryptoAllocationRouter(shared,ledger,account_id='pool',authorize=lambda *a:True)
    parent=CryptoWorkerController(worker,router,settings=SETTINGS)
    try:yield shared,ledger,router,parent
    finally:parent.close();ledger.close()


def drain(parent,quotes,now=NOW):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        result=parent.poll(quotes,now=now)
        if result['entry']!='PENDING_WORKER':return result
        time.sleep(.01)
    raise AssertionError('No parent completion')


def test_subprocess_is_pure_and_response_matches_parent(worker):
    h,q=data();raw=_pack(h,q,NOW,SETTINGS)
    assert worker.assess(h,q,NOW,SETTINGS)
    with pytest.raises(RuntimeError):worker.assess(h,q,NOW,SETTINGS)
    response=wait(worker)
    assert response['payload']==_assess(raw)
    assert response['payload']['proposal']['action']=='BUY'
    assert worker.status()['execution_enabled'] is False
    worker.submit('PING');assert wait(worker)['payload']['pid']==worker.pid


@pytest.mark.parametrize('case',['extra','bars','time','decimal','number','settings','future','pair','code'])
def test_bad_transport_never_leaves_ready(worker,case):
    h,q=data();raw=_pack(h,q,NOW,SETTINGS)
    if case=='extra':raw['ledger_path']='private.db'
    elif case=='bars':raw['bars']*=3
    elif case=='time':raw['at']='2026-10-13T15:00:00'
    elif case=='decimal':raw['quote']['bid']='NaN'
    elif case=='number':raw['quote']['ask']=101.1
    elif case=='settings':raw['settings']['notional_cap']='1000'
    elif case=='future':raw['bars'][-1]['end']=(NOW+timedelta(seconds=1)).isoformat()
    elif case=='pair':raw['symbol']='BTC/USDT'
    else:raw['settings']['python_code']='anything'
    with pytest.raises(ValueError):worker.submit('ASSESS',raw)
    assert worker.state==WorkerState.READY


def test_child_cannot_change_proposal_prices_or_identity():
    w=CryptoSignalWorker();h,q=data()
    w._expected='ASSESSED';w._expected_payload=_pack(h,q,NOW,SETTINGS)
    response=_assess(w._expected_payload)
    assert w._valid_payload(response)
    response['proposal']['stop']='1'
    assert not w._valid_payload(response)
    response=_assess(w._expected_payload);response['proposal']['id']='invented'
    assert not w._valid_payload(response)
    w.close()


@pytest.mark.parametrize('case,reason',[('short','NO_CONFIRMED_SPOT_BREAKOUT'),('missing','QUOTE_UNAVAILABLE'),
    ('stale','QUOTE_UNAVAILABLE'),('below','TRIGGER_NOT_CURRENT'),('extended','ENTRY_EXTENDED'),
    ('history','HISTORY_NOT_CURRENT_OR_CONTIGUOUS'),('few','INSUFFICIENT_HISTORY')])
def test_signal_refusals(case,reason):
    h,q=data()
    if case=='short':h=replace(h,bars=h.bars[:-1]+(replace(h.bars[-1],high=D(98),low=D(96),close=D(97)),))
    elif case=='missing':q=None
    elif case=='stale':q.timestamp=NOW-timedelta(seconds=3)
    elif case=='below':q.bid=D(100)
    elif case=='extended':q.bid,q.ask=D(110),D('110.1')
    elif case=='history':h=replace(h,bars=(replace(h.bars[0],end=h.bars[0].end-timedelta(minutes=1)),)+h.bars[1:])
    else:h=replace(h,bars=h.bars[:5])
    assert propose(h,_quote(q),NOW,SETTINGS)==(None,reason)


def test_proposal_cost_budgets_and_stable_signal_identity(tmp_path):
    h,q=data();p,reason=propose(h,_quote(q),NOW,SETTINGS)
    assert reason=='PROPOSED'
    ledger=CryptoPaper(tmp_path/'crypto.db')
    try:
        prepared=ledger.prepare_entry(p,q,now=NOW)
        assert prepared[8]<=SETTINGS.capital_budget and prepared[9]<=SETTINGS.risk_budget
        q.ask=D('101.15')
        again,_=propose(h,_quote(q),NOW,SETTINGS)
        assert again['id']==p['id'] and again['target']!=p['target']
    finally:ledger.close()


def test_default_off_then_shared_entry_and_protection_after_child_closed(system):
    shared,ledger,router,parent=system;h,q=data();quotes={'BTC/USD':q}
    assert parent.submit(h,quotes,now=NOW)['entry']=='PENDING_WORKER'
    assert drain(parent,quotes)['entry']=='ENTRIES_DISABLED'
    router.set_enabled(True)
    assert parent.submit(h,quotes,now=NOW)['entry']=='PENDING_WORKER'
    assert drain(parent,quotes)['entry']=='OPENED'
    assert shared.snapshot('pool')['active_commands']==1
    # Completion uses current parent prices; same successful signal never adds exposure.
    parent.submit(h,quotes,now=NOW)
    assert drain(parent,quotes)['entry']=='DUPLICATE_SIGNAL'
    parent.worker.close()
    q.bid,q.ask,q.timestamp=D(115),D('115.1'),NOW+timedelta(seconds=1)
    result=parent.poll(quotes,now=q.timestamp)
    assert result['entry']=='WORKER_UNAVAILABLE' and not router.enabled
    assert not ledger.snapshot()['positions'] and shared.snapshot('pool')['active_commands']==0


@pytest.mark.parametrize('case,reason',[('old','COMPLETION_EXPIRED'),('rewind','COMPLETION_EXPIRED'),
    ('policy','POLICY_CHANGED'),('quote','QUOTE_UNAVAILABLE'),('trigger','TRIGGER_NOT_CURRENT')])
def test_late_policy_or_current_quote_refuses_completion(system,case,reason):
    shared,ledger,router,parent=system;h,q=data();quotes={'BTC/USD':q};router.set_enabled(True)
    parent.submit(h,quotes,now=NOW)
    at=NOW
    if case=='old':at=NOW+timedelta(seconds=3);q.timestamp=at
    elif case=='rewind':at=NOW-timedelta(seconds=1)
    elif case=='policy':parent.settings=replace(SETTINGS,notional_cap=D(100))
    elif case=='quote':q.timestamp=NOW-timedelta(seconds=3)
    else:q.bid=D(100)
    assert drain(parent,quotes,at)['entry']==reason
    assert not ledger.snapshot()['positions'] and shared.snapshot('pool')['active_commands']==0


def test_parent_recomputes_against_new_quote_before_admission(system):
    _,ledger,router,parent=system;h,q=data();quotes={'BTC/USD':q};router.set_enabled(True)
    parent.submit(h,quotes,now=NOW)
    q.ask=D('101.15')
    assert drain(parent,quotes)['entry']=='OPENED'
    assert D(ledger.snapshot()['positions'][0]['entry'])==q.ask*(1+ledger.slippage)*(1+ledger.fee)


def test_signal_cost_settings_must_match_native_ledger(system):
    _,_,router,parent=system
    with pytest.raises(ValueError,match='costs'):
        CryptoWorkerController(parent.worker,router,settings=replace(SETTINGS,fee=D('.002')))


def test_entry_revoked_while_child_pending_is_not_restored(system):
    shared,ledger,router,parent=system;h,q=data();quotes={'BTC/USD':q};router.set_enabled(True)
    parent.submit(h,quotes,now=NOW)
    router.set_enabled(False)
    assert drain(parent,quotes)['entry']=='ENTRIES_DISABLED'
    assert not ledger.snapshot()['positions'] and shared.snapshot('pool')['active_commands']==0


def test_cost_model_change_refuses_pending_entry(system):
    _,ledger,router,parent=system;h,q=data();quotes={'BTC/USD':q};router.set_enabled(True)
    parent.submit(h,quotes,now=NOW)
    ledger.fee=D('.002')
    assert drain(parent,quotes)['entry']=='COST_MODEL_CHANGED'
    assert not ledger.snapshot()['positions'] and not router.enabled


def test_unknown_dispatch_failure_keeps_shared_hold_and_never_retries(system,monkeypatch):
    shared,ledger,router,parent=system;h,q=data();quotes={'BTC/USD':q};router.set_enabled(True)
    def broken(*a,**k):raise RuntimeError('uncertain dispatch')
    monkeypatch.setattr(ledger,'apply',broken)
    parent.submit(h,quotes,now=NOW)
    with pytest.raises(RuntimeError,match='uncertain'):drain(parent,quotes)
    assert parent.pending is None and shared.snapshot('pool')['active_commands']==1
    parent.submit(h,quotes,now=NOW)
    assert drain(parent,quotes)['entry']=='ALLOCATION_RECONCILIATION_REQUIRED'
    assert not ledger.snapshot()['positions'] and shared.snapshot('pool')['active_commands']==1
