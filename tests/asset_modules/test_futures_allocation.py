from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D, localcontext

import pytest

from app.asset_modules.admission_controller import EntryReservation, FuturesEntryReservation, PaperAdmissionController
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.futures_allocation import FuturesAllocationRouter
from app.asset_modules.futures_paper import FuturesPaper
from app.asset_modules.futures_signals import FuturesPaperController, propose
from tests.asset_modules.test_futures_signals import NOW, SETTINGS, data


def inputs(side='LONG'):
    c,h,q = data(side)
    h = replace(h,bars=tuple(replace(b,high=b.high+D(4900),low=b.low+D(4900),close=b.close+D(4900)) for b in h.bars))
    q = replace(q,bid=q.bid+D(4900),ask=q.ask+D(4900))
    candidate,reason = propose(c,h,q,NOW,SETTINGS)
    assert reason=='PROPOSED'
    return c,h,q,candidate


def equity(identity='equity', *, capital=D(1000), risk=D(10)):
    return EntryReservation(identity,'pool',EngineId.WARRIOR,'WARRIOR_MOMENTUM_V1|SPY|'+identity,
        'SPY','synthetic-equity-v1','BUY',D(10),capital/D(10),capital/D(10)-D('.10'),D(1),
        capital,risk,NOW,NOW+timedelta(seconds=30))


@pytest.fixture
def system(tmp_path):
    shared = PaperAdmissionController(tmp_path/'shared.sqlite3')
    shared.configure_account('pool',capital=D(3000),loss_budget=D(50))
    ledger = FuturesPaper(tmp_path/'futures.sqlite3')
    ledger.set_enabled(True)
    router = FuturesAllocationRouter(shared,ledger,account_id='pool')
    parent = FuturesPaperController(ledger,settings=SETTINGS,authorize=lambda *args:True,entry_router=router)
    try:
        yield shared,ledger,router,parent
    finally:
        ledger.close()


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_shared_margin_competes_with_equity_not_futures_notional(system,side):
    shared,ledger,router,parent = system
    c,h,q,p = inputs(side)
    r = router.reservation(p,q)
    assert isinstance(r,FuturesEntryReservation)
    assert r.capital==D(2009) and r.loss_budget==D(19)
    assert r.capital < r.entry_limit*r.multiplier*r.quantity
    assert shared.reserve(equity(),now=NOW).state=='RESERVED'
    assert parent.step(c,h,{c.symbol:q},now=NOW)['entry']=='SHARED_CAPITAL_LIMIT'
    assert ledger.snapshot()['positions']==[]
    shared.abandon_unsubmitted('equity',EngineId.WARRIOR)
    assert parent.step(c,h,{c.symbol:q},now=NOW)['entry']=='OPENED'
    assert shared.snapshot('pool')['reserved_capital']==D(2009)
    assert shared.reserve(equity('another'),now=NOW).reason=='SHARED_CAPITAL_LIMIT'
    assert shared.reserve(equity('fits',capital=D(980)),now=NOW).state=='RESERVED'
    assert shared.snapshot('pool')['reserved_capital']==D(2989)
    assert shared.command('pool',p.intent_id)['state']=='ACKNOWLEDGED'
    assert shared.command('other',p.intent_id) is None
    assert ledger.enter('outside',p.plan,q,now=NOW)=='ALLOCATION_ROUTER_REQUIRED'


@pytest.mark.parametrize('changes',[
    {'capital':D(2000)}, {'loss_budget':D(18)}, {'margin_per_contract':D(0)},
    {'execution_cost_budget':D('-1')}, {'execution_cost_budget':D('NaN')},
    {'execution_cost_budget':0}, {'quantity':D('1.5')}, {'quantity':D(6)},
    {'ledger_id':'wrong'}, {'plan_fingerprint':'wrong'},
    {'engine':EngineId.SCALPER,'lifecycle_id':'QUICK_SCALPER|SPY|one'},
])
def test_futures_margin_model_cannot_weaken_other_engine_or_cost_requirements(system,changes):
    _,_,router,_ = system
    _,_,q,p = inputs()
    r = router.reservation(p,q)
    with pytest.raises(ValueError): replace(r,**changes)


def test_full_notional_requests_and_unbound_margin_requests_keep_their_requirements(system,tmp_path):
    _,_,router,_ = system
    _,_,q,p = inputs()
    r = router.reservation(p,q)
    fields = {name:getattr(r,name) for name in EntryReservation.__dataclass_fields__}
    with pytest.raises(ValueError): EntryReservation(**fields)
    other = PaperAdmissionController(tmp_path/'other.sqlite3')
    other.configure_account('pool',capital=D(3000),loss_budget=D(50))
    assert other.reserve(r,now=NOW).reason=='FUTURES_LEDGER_NOT_BOUND'


def test_shared_loss_budget_and_local_ledger_risk_both_remain_authoritative(tmp_path):
    shared = PaperAdmissionController(tmp_path/'shared.sqlite3')
    shared.configure_account('pool',capital=D(10000),loss_budget=D(20))
    ledger = FuturesPaper(tmp_path/'f.sqlite3',risk_per_trade=D(5))
    ledger.set_enabled(True)
    router = FuturesAllocationRouter(shared,ledger,account_id='pool')
    parent = FuturesPaperController(ledger,settings=SETTINGS,authorize=lambda *args:True,entry_router=router)
    c,h,q,p = inputs()
    shared.reserve(equity(risk=D(5)),now=NOW)
    assert parent.step(c,h,{c.symbol:q},now=NOW)['entry']=='SHARED_LOSS_BUDGET'
    shared.abandon_unsubmitted('equity',EngineId.WARRIOR)
    assert parent.step(c,h,{c.symbol:q},now=NOW)['entry']=='RISK_LIMIT'
    assert shared.command('pool',p.intent_id)['state']=='REJECTED'
    assert shared.snapshot('pool')['active_commands']==0 and not ledger.snapshot()['positions']
    assert parent.step(c,h,{c.symbol:q},now=NOW)['entry']=='ALLOCATION_COMMAND_EXISTS'
    ledger.close()


@pytest.mark.parametrize('exit_kind,pnl',[('STOP',D(-19)),('TARGET',D(21))])
def test_verified_exit_releases_once_and_books_realized_losses_without_compounding(system,exit_kind,pnl):
    shared,ledger,router,parent = system
    c,h,q,p = inputs()
    assert parent.step(c,h,{c.symbol:q},now=NOW)['entry']=='OPENED'
    with pytest.raises(ValueError,match='settlement'):
        shared.reconcile_flat(p.intent_id,EngineId.FUTURES,orders_terminal=True,remaining_quantity=D(0),reference='unverified')
    assert router.reconcile()[p.intent_id]=='HELD'
    assert shared.snapshot('pool')['reserved_capital']==D(2009)
    ledger.set_enabled(False)
    later = NOW+timedelta(seconds=1)
    price = p.plan.stop if exit_kind=='STOP' else p.plan.target
    closing = replace(q,bid=price,ask=price+c.tick,source_at=later,observed_at=later)
    assert parent.protect({c.symbol:closing},now=later)[p.intent_id]==exit_kind
    assert shared.command('pool',p.intent_id)['state']=='CLOSED'
    assert shared.snapshot('pool')['active_commands']==0
    balance = shared.allocation_balance('pool')
    assert balance['futures_realized_pnl']==pnl
    assert balance['capital_ceiling']==D(3000)+min(pnl,D(0))
    assert router.reconcile()=={}
    assert shared.allocation_balance('pool')==balance
    assert shared.reserve(equity('backdated',capital=D(500)),now=NOW).reason=='ALLOCATION_EVENT_OUT_OF_ORDER'
    # All engines see the reduced shared ceiling after a futures loss.
    if exit_kind=='STOP':
        assert shared.reserve(equity('too-large',capital=D(2990)),now=later).reason=='SHARED_CAPITAL_LIMIT'
    r = router.reservation(p,q)
    with pytest.raises(ValueError):
        shared.settle_futures_flat(p.intent_id,ledger_id=ledger.ledger_id,plan_fingerprint=r.plan_fingerprint,net_pnl=pnl+D(1),settled_at=later,reference='changed')


def test_ack_crash_recovers_matching_open_intent_without_a_second_entry(system,monkeypatch):
    shared,ledger,router,parent = system
    c,h,q,p = inputs()
    original = shared.acknowledge
    def crash(*args): raise RuntimeError('synthetic interrupted ack')
    monkeypatch.setattr(shared,'acknowledge',crash)
    with pytest.raises(RuntimeError): parent.step(c,h,{c.symbol:q},now=NOW)
    assert ledger.intent_state(p.intent_id)=='OPEN'
    assert shared.snapshot('pool')['uncertain_submissions']==1
    monkeypatch.setattr(shared,'acknowledge',original)
    restarted = PaperAdmissionController(shared.path)
    recovery = FuturesAllocationRouter(restarted,ledger,account_id='pool')
    assert recovery.reconcile()[p.intent_id]=='HELD'
    assert restarted.snapshot('pool')['uncertain_submissions']==0
    assert len(ledger.snapshot()['positions'])==1


def test_absence_after_claim_is_uncertain_and_never_frees_budget(system,monkeypatch):
    shared,ledger,router,parent = system
    c,h,q,p = inputs()
    original = ledger.enter
    def crash(*args,**kwargs): raise RuntimeError('synthetic interrupted dispatch')
    monkeypatch.setattr(ledger,'enter',crash)
    with pytest.raises(RuntimeError): parent.step(c,h,{c.symbol:q},now=NOW)
    monkeypatch.setattr(ledger,'enter',original)
    assert ledger.intent_state(p.intent_id) is None
    assert router.reconcile()[p.intent_id]=='UNCERTAIN'
    assert shared.snapshot('pool')['reserved_capital']==D(2009)
    assert parent.step(c,h,{c.symbol:q},now=NOW)['entry']=='ALLOCATION_RECONCILIATION_REQUIRED'
    assert not shared.claim_dispatch(p.intent_id,EngineId.FUTURES,now=NOW+timedelta(days=1))
    assert shared.snapshot('pool')['reserved_capital']==D(2009)


def test_settlement_crash_recovers_closed_evidence_once(system,monkeypatch):
    shared,ledger,router,parent = system
    c,h,q,p = inputs()
    parent.step(c,h,{c.symbol:q},now=NOW)
    original = shared.settle_futures_flat
    def crash(*args,**kwargs): raise RuntimeError('synthetic interrupted settlement')
    monkeypatch.setattr(shared,'settle_futures_flat',crash)
    later = NOW+timedelta(seconds=1)
    q = replace(q,bid=p.plan.stop,ask=p.plan.stop+c.tick,source_at=later,observed_at=later)
    with pytest.raises(RuntimeError): parent.protect({c.symbol:q},now=later)
    assert ledger.intent_state(p.intent_id)=='CLOSED'
    assert shared.snapshot('pool')['reserved_capital']==D(2009)
    monkeypatch.setattr(shared,'settle_futures_flat',original)
    assert router.reconcile()[p.intent_id]=='CLOSED'
    assert shared.allocation_balance('pool')['futures_realized_pnl']==D(-19)
    assert router.reconcile()=={}


def test_wrong_ledger_or_account_binding_is_rejected_and_ids_survive_restart(system,tmp_path):
    shared,ledger,router,_ = system
    identity = ledger.ledger_id
    ledger.close()
    restored = FuturesPaper(tmp_path/'futures.sqlite3')
    try:
        assert restored.ledger_id==identity
        FuturesAllocationRouter(shared,restored,account_id='pool')
        shared.configure_account('other',capital=D(3000),loss_budget=D(50))
        with pytest.raises(ValueError): FuturesAllocationRouter(shared,restored,account_id='other')
        another = FuturesPaper(tmp_path/'second.sqlite3')
        try:
            with pytest.raises(ValueError): FuturesAllocationRouter(shared,another,account_id='pool')
        finally: another.close()
    finally:
        restored.close()
    # Fixture owns the old handle; closing twice is supported by sqlite3.


def test_unmanaged_positions_cannot_be_bound_into_a_shared_pool(tmp_path):
    shared = PaperAdmissionController(tmp_path/'s.sqlite3')
    shared.configure_account('pool',capital=D(3000),loss_budget=D(50))
    ledger = FuturesPaper(tmp_path/'f.sqlite3'); ledger.set_enabled(True)
    c,h,q,p = inputs()
    assert ledger.enter('unmanaged',p.plan,q,now=NOW)=='OPENED'
    with pytest.raises(ValueError,match='existing futures exposure'):
        FuturesAllocationRouter(shared,ledger,account_id='pool')
    ledger.close()


def test_concurrent_engines_cannot_overallocate_and_small_context_cannot_hide_it(system):
    shared,_,router,_ = system
    _,_,q,p = inputs()
    request = router.reservation(p,q)
    def reserve(r): return PaperAdmissionController(shared.path).reserve(r,now=NOW).state
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(reserve,(request,equity())))
    assert sorted(states)==['REJECTED','RESERVED']
    more = replace(equity('more',capital=D(2999)),instrument='QQQ')
    with localcontext() as ctx:
        ctx.prec = 1
        rejected = shared.reserve(more,now=NOW)
        assert rejected.reason=='SHARED_CAPITAL_LIMIT'
