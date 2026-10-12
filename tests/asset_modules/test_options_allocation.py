from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D, localcontext

import pytest

from app.asset_modules.admission_controller import OptionsEntryReservation, PaperAdmissionController
from app.asset_modules.options_allocation import OptionsAllocationRouter
from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.options_signals import OptionsPaperController, propose
from app.asset_modules.engine_catalog import EngineId
from tests.asset_modules.test_options_signals import NOW, SETTINGS, data
from tests.asset_modules.test_futures_allocation import equity, inputs as futures_inputs
from app.asset_modules.futures_allocation import FuturesAllocationRouter
from app.asset_modules.futures_paper import FuturesPaper


def inputs():
    h,uq,cs,qs = data()
    proposals,reason = propose(h,uq,cs,qs,NOW,SETTINGS)
    assert reason == 'PROPOSED'
    return h,uq,cs,qs,proposals[0]


@pytest.fixture
def system(tmp_path):
    shared = PaperAdmissionController(tmp_path/'shared.sqlite3')
    shared.configure_account('pool',capital=D(3000),loss_budget=D(50))
    ledger = OptionsPaper(tmp_path/'options.sqlite3'); ledger.set_enabled(True)
    router = OptionsAllocationRouter(shared,ledger,account_id='pool')
    parent = OptionsPaperController(ledger,settings=SETTINGS,authorize=lambda *a:True,entry_router=router)
    try:
        yield shared,ledger,router,parent
    finally:
        ledger.close()


def test_full_premium_costs_compete_with_equity_and_futures(system,tmp_path):
    shared,ledger,router,parent = system
    h,uq,cs,qs,p = inputs()
    q = qs[p.plan.contract.symbol]
    r = router.reservation(p,q)
    assert isinstance(r,OptionsEntryReservation)
    assert r.capital == D('813.2') and r.loss_budget == D('21.2')
    assert shared.reserve(equity(capital=D(2200)),now=NOW).state == 'RESERVED'
    assert parent.step(h,uq,cs,qs,now=NOW)['entry'] == 'SHARED_CAPITAL_LIMIT'
    shared.abandon_unsubmitted('equity',EngineId.WARRIOR)
    fl = FuturesPaper(tmp_path/'f.sqlite3'); fl.set_enabled(True)
    fr = FuturesAllocationRouter(shared,fl,account_id='pool')
    _,_,fq,fp = futures_inputs()
    assert fr.submit(fp,fq,now=NOW) == 'OPENED'
    assert parent.step(h,uq,cs,qs,now=NOW)['entry'] == 'OPENED'
    assert shared.snapshot('pool')['reserved_capital'] == D('2822.2')
    assert shared.reserve(equity('another',capital=D(200)),now=NOW).reason == 'SHARED_CAPITAL_LIMIT'
    assert ledger.enter('outside',p.plan,q,now=NOW) == 'ALLOCATION_ROUTER_REQUIRED'
    assert shared.command('pool',p.intent_id)['state'] == 'ACKNOWLEDGED'
    assert shared.command('other',p.intent_id) is None
    fl.close()


@pytest.mark.parametrize('changes', [
    {'capital':D(800)}, {'loss_budget':D(20)}, {'execution_cost_budget':D('-1')},
    {'execution_cost_budget':D('NaN')}, {'execution_cost_budget':0},
    {'quantity':D('1.5')}, {'quantity':D(6)}, {'multiplier':D(1)},
    {'ledger_id':'wrong'}, {'plan_fingerprint':'wrong'},
    {'engine':EngineId.SCALPER,'lifecycle_id':'QUICK_SCALPER|SPY|one'},
    {'side':'SELL','structural_stop':D('2.02')},
])
def test_typed_premium_model_keeps_ownership_and_full_cost_requirements(system,changes):
    _,_,router,_ = system
    _,_,_,qs,p = inputs()
    with pytest.raises(ValueError): replace(router.reservation(p,qs[p.plan.contract.symbol]),**changes)


def test_unbound_reservation_rejected(system,tmp_path):
    _,_,router,_ = system
    _,_,_,qs,p = inputs()
    other = PaperAdmissionController(tmp_path/'other.sqlite3')
    other.configure_account('pool',capital=D(3000),loss_budget=D(50))
    assert other.reserve(router.reservation(p,qs[p.plan.contract.symbol]),now=NOW).reason == 'OPTIONS_LEDGER_NOT_BOUND'


def test_local_refusal_releases_claim_but_does_not_retry(tmp_path):
    shared = PaperAdmissionController(tmp_path/'s.sqlite3')
    shared.configure_account('pool',capital=D(3000),loss_budget=D(50))
    ledger = OptionsPaper(tmp_path/'o.sqlite3',risk_per_trade=D(5)); ledger.set_enabled(True)
    router = OptionsAllocationRouter(shared,ledger,account_id='pool')
    parent = OptionsPaperController(ledger,settings=SETTINGS,authorize=lambda *a:True,entry_router=router)
    h,uq,cs,qs,p = inputs()
    assert parent.step(h,uq,cs,qs,now=NOW)['entry'] == 'RISK_LIMIT'
    assert shared.command('pool',p.intent_id)['state'] == 'REJECTED'
    assert shared.snapshot('pool')['active_commands'] == 0
    assert parent.step(h,uq,cs,qs,now=NOW)['entry'] == 'ALLOCATION_COMMAND_EXISTS'
    ledger.close()


@pytest.mark.parametrize('exit_kind,pnl', [('STOP',D('-21.2')),('TARGET',D('26.8'))])
def test_verified_settlement_releases_once_and_updates_ceiling(system,exit_kind,pnl):
    shared,ledger,router,parent = system
    h,uq,cs,qs,p = inputs()
    assert parent.step(h,uq,cs,qs,now=NOW)['entry'] == 'OPENED'
    with pytest.raises(ValueError,match='settlement'):
        shared.reconcile_flat(p.intent_id,EngineId.OPTIONS,orders_terminal=True,remaining_quantity=D(0),reference='unverified')
    assert router.reconcile()[p.intent_id] == 'HELD'
    ledger.set_enabled(False)
    later = NOW+timedelta(seconds=1)
    price = p.plan.stop if exit_kind == 'STOP' else p.plan.target
    q = replace(qs[p.plan.contract.symbol],bid=price,ask=price+D('.01'),source_at=later,observed_at=later)
    assert parent.protect({q.symbol:q},now=later)[p.intent_id] == exit_kind
    balance = shared.allocation_balance('pool')
    assert balance['options_realized_pnl'] == pnl and balance['derivatives_realized_pnl'] == pnl
    assert balance['capital_ceiling'] == D(3000)+min(pnl,D(0))
    assert shared.snapshot('pool')['active_commands'] == 0
    assert router.reconcile() == {} and shared.allocation_balance('pool') == balance
    assert shared.reserve(equity('backdated'),now=NOW).reason == 'ALLOCATION_EVENT_OUT_OF_ORDER'
    r = router.reservation(p,q)
    with pytest.raises(ValueError):
        shared.settle_options_flat(p.intent_id,ledger_id=ledger.ledger_id,plan_fingerprint=r.plan_fingerprint,
                                  net_pnl=pnl+D(1),settled_at=later,reference='changed')


def test_ack_crash_recovers_matching_open_position(system,monkeypatch):
    shared,ledger,router,parent = system
    h,uq,cs,qs,p = inputs()
    original = shared.acknowledge
    monkeypatch.setattr(shared,'acknowledge',lambda *a: (_ for _ in ()).throw(RuntimeError('interrupted ack')))
    with pytest.raises(RuntimeError): parent.step(h,uq,cs,qs,now=NOW)
    assert ledger.intent_state(p.intent_id) == 'OPEN'
    monkeypatch.setattr(shared,'acknowledge',original)
    restored = PaperAdmissionController(shared.path)
    recovery = OptionsAllocationRouter(restored,ledger,account_id='pool')
    assert recovery.reconcile()[p.intent_id] == 'HELD'
    assert restored.snapshot('pool')['uncertain_submissions'] == 0
    assert len(ledger.snapshot()['positions']) == 1


def test_absence_after_claim_does_not_release_or_retry(system,monkeypatch):
    shared,ledger,router,parent = system
    h,uq,cs,qs,p = inputs()
    original = ledger.enter
    monkeypatch.setattr(ledger,'enter',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('interrupted dispatch')))
    with pytest.raises(RuntimeError): parent.step(h,uq,cs,qs,now=NOW)
    monkeypatch.setattr(ledger,'enter',original)
    assert router.reconcile()[p.intent_id] == 'UNCERTAIN'
    assert shared.snapshot('pool')['reserved_capital'] == D('813.2')
    assert parent.step(h,uq,cs,qs,now=NOW)['entry'] == 'ALLOCATION_RECONCILIATION_REQUIRED'


def test_settlement_crash_recovers_closed_evidence_once(system,monkeypatch):
    shared,ledger,router,parent = system
    h,uq,cs,qs,p = inputs()
    parent.step(h,uq,cs,qs,now=NOW)
    original = shared.settle_options_flat
    monkeypatch.setattr(shared,'settle_options_flat',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('interrupted settlement')))
    later = NOW+timedelta(seconds=1)
    q = replace(qs[p.plan.contract.symbol],bid=p.plan.stop,ask=p.plan.stop+D('.01'),source_at=later,observed_at=later)
    with pytest.raises(RuntimeError): parent.protect({q.symbol:q},now=later)
    assert ledger.intent_state(p.intent_id) == 'CLOSED'
    assert shared.snapshot('pool')['reserved_capital'] == D('813.2')
    monkeypatch.setattr(shared,'settle_options_flat',original)
    assert router.reconcile()[p.intent_id] == 'CLOSED'
    assert shared.allocation_balance('pool')['options_realized_pnl'] == D('-21.2')
    assert router.reconcile() == {}


def test_binding_identity_survives_restart_and_rejects_other_pools(system,tmp_path):
    shared,ledger,_,_ = system
    identity = ledger.ledger_id
    ledger.close()
    restored = OptionsPaper(tmp_path/'options.sqlite3')
    try:
        assert restored.ledger_id == identity
        OptionsAllocationRouter(shared,restored,account_id='pool')
        shared.configure_account('other',capital=D(3000),loss_budget=D(50))
        with pytest.raises(ValueError): OptionsAllocationRouter(shared,restored,account_id='other')
        second = OptionsPaper(tmp_path/'second.sqlite3')
        try:
            with pytest.raises(ValueError): OptionsAllocationRouter(shared,second,account_id='pool')
        finally: second.close()
    finally: restored.close()


def test_unmanaged_position_cannot_be_adopted(tmp_path):
    shared = PaperAdmissionController(tmp_path/'s.sqlite3')
    shared.configure_account('pool',capital=D(3000),loss_budget=D(50))
    ledger = OptionsPaper(tmp_path/'o.sqlite3'); ledger.set_enabled(True)
    _,_,_,qs,p = inputs()
    assert ledger.enter('unmanaged',p.plan,qs[p.plan.contract.symbol],now=NOW) == 'OPENED'
    with pytest.raises(ValueError,match='existing options exposure'): OptionsAllocationRouter(shared,ledger,account_id='pool')
    ledger.close()


def test_concurrent_engines_cannot_overallocate_under_small_decimal_context(system):
    shared,_,router,_ = system
    _,_,_,qs,p = inputs()
    request = router.reservation(p,qs[p.plan.contract.symbol])
    def reserve(r): return PaperAdmissionController(shared.path).reserve(r,now=NOW).state
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(reserve,(request,equity(capital=D(2200)))))
    assert sorted(states) == ['REJECTED','RESERVED']
    more = replace(equity('more',capital=D(2999)),instrument='QQQ')
    with localcontext() as ctx:
        ctx.prec = 1
        assert shared.reserve(more,now=NOW).reason == 'SHARED_CAPITAL_LIMIT'


def test_options_and_futures_settlements_share_loss_adjusted_ceiling(system,tmp_path):
    shared,_,_,parent = system
    h,uq,cs,qs,p = inputs()
    fl = FuturesPaper(tmp_path/'f.sqlite3'); fl.set_enabled(True)
    fr = FuturesAllocationRouter(shared,fl,account_id='pool')
    fc,_,fq,fp = futures_inputs()
    assert fr.submit(fp,fq,now=NOW) == 'OPENED'
    assert parent.step(h,uq,cs,qs,now=NOW)['entry'] == 'OPENED'
    later = NOW+timedelta(seconds=1)
    oq = replace(qs[p.plan.contract.symbol],bid=p.plan.stop,ask=p.plan.stop+D('.01'),source_at=later,observed_at=later)
    parent.protect({oq.symbol:oq},now=later)
    assert shared.allocation_balance('pool')['capital_ceiling'] == D('2978.8')
    later += timedelta(seconds=1)
    fq = replace(fq,bid=fp.plan.target,ask=fp.plan.target+fc.tick,source_at=later,observed_at=later)
    assert fl.protect({fc.symbol:fq},now=later)[fp.intent_id] == 'TARGET'
    assert fr.reconcile()[fp.intent_id] == 'CLOSED'
    balance = shared.allocation_balance('pool')
    assert balance['options_realized_pnl'] == D('-21.2')
    assert balance['futures_realized_pnl'] == D(21)
    assert balance['derivatives_realized_pnl'] == D('-.2')
    assert balance['capital_ceiling'] == D('2999.8')
    assert shared.snapshot('pool')['active_commands'] == 0
    assert shared.reserve(equity('backdated'),now=NOW+timedelta(seconds=1)).reason == 'ALLOCATION_EVENT_OUT_OF_ORDER'
    fl.close()


def test_legacy_options_ledger_upgrades_without_adopting_existing_exposure(tmp_path):
    import sqlite3
    path = tmp_path/'legacy.sqlite3'
    ledger = OptionsPaper(path); ledger.set_enabled(True)
    _,_,_,qs,p = inputs()
    assert ledger.enter('legacy-open',p.plan,qs[p.plan.contract.symbol],now=NOW) == 'OPENED'
    ledger.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE settlements')
        db.execute('DELETE FROM metadata WHERE id=2')
    restored = OptionsPaper(path)
    shared = PaperAdmissionController(tmp_path/'s.sqlite3')
    shared.configure_account('pool',capital=D(3000),loss_budget=D(50))
    try:
        assert not restored.enabled and len(restored.snapshot()['positions']) == 1
        with pytest.raises(ValueError): OptionsAllocationRouter(shared,restored,account_id='pool')
        later = NOW+timedelta(seconds=1)
        q = replace(qs[p.plan.contract.symbol],bid=p.plan.stop,ask=p.plan.stop+D('.01'),source_at=later,observed_at=later)
        assert restored.protect({q.symbol:q},now=later)['legacy-open'] == 'STOP'
        assert restored.intent_evidence('legacy-open')['net_pnl'] == D('-21.2')
        # Binding does not automatically import old standalone gains or losses.
        OptionsAllocationRouter(shared,restored,account_id='pool')
        assert shared.allocation_balance('pool')['options_realized_pnl'] == 0
    finally: restored.close()
