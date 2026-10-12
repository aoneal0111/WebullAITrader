from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import pytest

from app.asset_modules.admission_controller import PaperAdmissionController
from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.engine_catalog import EngineId
from tests.asset_modules.test_markets import buy,quote
from tests.asset_modules.test_futures_allocation import equity,NOW


@pytest.fixture
def system(tmp_path):
    shared=PaperAdmissionController(tmp_path/'s.sqlite3')
    shared.configure_account('pool',capital=D(1000),loss_budget=D(50))
    ledger=CryptoPaper(tmp_path/'c.sqlite3')
    router=CryptoAllocationRouter(shared,ledger,account_id='pool',authorize=lambda *a:True)
    q=replace(quote(),timestamp=NOW)
    try: yield shared,ledger,router,q
    finally: ledger.close()


def test_default_off_full_spot_capital_and_equity_competition(system):
    shared,ledger,router,q=system
    assert router.submit(buy(),{'BTC/USD':q},now=NOW)['entry']=='ENTRIES_DISABLED'
    r,expected=router.reservation(buy(),q,now=NOW)
    assert r.capital>=expected[8] and r.loss_budget>=expected[9]
    assert r.capital>D(200) and r.multiplier==1 and r.quantity.as_tuple().exponent==-8
    assert shared.reserve(equity(capital=D(800)),now=NOW).state=='RESERVED'
    router.set_enabled(True)
    assert router.submit(buy(),{'BTC/USD':q},now=NOW)['entry']=='SHARED_CAPITAL_LIMIT'
    shared.abandon_unsubmitted('equity',EngineId.WARRIOR)
    assert router.submit(buy(),{'BTC/USD':q},now=NOW)['entry']=='OPENED'
    assert shared.snapshot('pool')['reserved_capital']==r.capital
    with pytest.raises(ValueError,match='router'): ledger.apply({**buy(),'id':'outside'},{'BTC/USD':q},now=NOW)
    assert router.submit(buy(),{'BTC/USD':q},now=NOW)['entry']=='DUPLICATE_SIGNAL'


@pytest.mark.parametrize('changes',[{'capital':D(199)},{'loss_budget':D(1)},
    {'execution_cost_budget':D(-1)},{'ledger_id':'bad'},{'plan_fingerprint':'bad'},
    {'engine':EngineId.WARRIOR,'lifecycle_id':'WARRIOR_MOMENTUM_V1|one'},
    {'side':'SELL','structural_stop':D(101)},{'multiplier':D(100)}])
def test_typed_spot_request_cannot_weaken_cost_or_owner(system,changes):
    _,_,router,q=system
    r,_=router.reservation(buy(),q,now=NOW)
    with pytest.raises(ValueError): replace(r,**changes)


def test_strict_authorization_and_local_cash_refusal(system):
    shared,ledger,router,q=system;router.set_enabled(True)
    router.authorize=lambda *a:1
    assert router.submit(buy(),{'BTC/USD':q},now=NOW)['entry']=='ENTRY_NOT_AUTHORIZED'
    router.authorize=lambda *a:True
    ledger.db.execute("UPDATE account SET cash='100'");ledger.db.commit()
    assert router.submit(buy(),{'BTC/USD':q},now=NOW)['entry']=='LOCAL_ENTRY_REFUSED'
    assert shared.snapshot('pool')['active_commands']==0
    r,_=router.reservation(buy(),q,now=NOW)
    assert shared.command('pool',r.command_id)['state']=='REJECTED'


def test_quote_change_after_authorization_refuses_and_frees_known_claim(system):
    shared,ledger,router,q=system;router.set_enabled(True)
    quotes={'BTC/USD':q}
    def authorize(*a):
        quotes['BTC/USD']=replace(q,ask=D('100.1'))
        return True
    router.authorize=authorize
    assert router.submit(buy(),quotes,now=NOW)['entry']=='LOCAL_ENTRY_REFUSED'
    assert not ledger.snapshot()['positions'] and shared.snapshot('pool')['active_commands']==0


@pytest.mark.parametrize('exit_price', [D(97),D(110)])
def test_final_settlement_releases_once_and_adjusts_all_engine_ceiling(system,exit_price):
    shared,ledger,router,q=system;router.set_enabled(True)
    router.submit(buy(),{'BTC/USD':q},now=NOW)
    r,_=router.reservation(buy(),q,now=NOW)
    with pytest.raises(ValueError,match='settlement'):
        shared.reconcile_flat(r.command_id,EngineId.CRYPTO,orders_terminal=True,remaining_quantity=D(0),reference='unverified')
    router.set_enabled(False)
    later=NOW+timedelta(seconds=1);closing=replace(q,timestamp=later,bid=exit_price,ask=exit_price+D(1))
    result=router.protect({'BTC/USD':closing},now=later)
    assert result['reconciliation'][r.command_id]=='CLOSED'
    assert shared.snapshot('pool')['active_commands']==0
    e=ledger.intent_evidence('one');balance=shared.allocation_balance('pool')
    assert balance['crypto_realized_pnl']==e['realized_pnl']
    assert balance['capital_ceiling']==D(1000)+min(e['realized_pnl'],D(0))
    assert router.reconcile()=={} and shared.allocation_balance('pool')==balance
    assert shared.reserve(equity('backdated'),now=NOW).reason=='ALLOCATION_EVENT_OUT_OF_ORDER'


def test_partial_exit_keeps_whole_reservation_until_flat(system):
    shared,ledger,router,q=system;router.set_enabled(True)
    router.submit(buy(),{'BTC/USD':q},now=NOW)
    r,_=router.reservation(buy(),q,now=NOW)
    later=NOW+timedelta(seconds=1);q=replace(q,timestamp=later,bid=D(105),ask=D(106))
    ledger.apply({'id':'partial','action':'SELL','symbol':'BTC/USD','fraction':'.5'},{'BTC/USD':q},now=later)
    assert router.reconcile()[r.command_id]=='HELD'
    assert shared.snapshot('pool')['reserved_capital']==r.capital
    assert shared.allocation_balance('pool')['crypto_realized_pnl']==0
    later+=timedelta(seconds=1);q=replace(q,timestamp=later,bid=D(110),ask=D(111))
    router.protect({'BTC/USD':q},now=later)
    assert shared.allocation_balance('pool')['crypto_realized_pnl']==ledger.intent_evidence('one')['realized_pnl']
    assert shared.snapshot('pool')['active_commands']==0


def test_interrupted_ack_recovers_without_duplicate(system,monkeypatch):
    shared,ledger,router,q=system;router.set_enabled(True)
    original=shared.acknowledge
    def crash(*a): raise RuntimeError('synthetic ack interruption')
    monkeypatch.setattr(shared,'acknowledge',crash)
    with pytest.raises(RuntimeError): router.submit(buy(),{'BTC/USD':q},now=NOW)
    assert ledger.intent_evidence('one')['state']=='OPEN'
    monkeypatch.setattr(shared,'acknowledge',original)
    recovery=CryptoAllocationRouter(PaperAdmissionController(shared.path),ledger,account_id='pool',authorize=lambda *a:True)
    r,_=router.reservation(buy(),q,now=NOW)
    assert recovery.reconcile()[r.command_id]=='HELD'
    assert not recovery.enabled and len(ledger.snapshot()['positions'])==1


def test_unknown_dispatch_exception_keeps_allocation(system,monkeypatch):
    shared,ledger,router,q=system;router.set_enabled(True)
    original=ledger.apply
    def crash(*a,**k): raise ValueError('unknown interrupted dispatch')
    monkeypatch.setattr(ledger,'apply',crash)
    with pytest.raises(ValueError): router.submit(buy(),{'BTC/USD':q},now=NOW)
    monkeypatch.setattr(ledger,'apply',original)
    r,_=router.reservation(buy(),q,now=NOW)
    assert router.reconcile()[r.command_id]=='UNCERTAIN'
    assert shared.snapshot('pool')['reserved_capital']==r.capital
    assert router.submit(buy(),{'BTC/USD':q},now=NOW)['entry']=='ALLOCATION_RECONCILIATION_REQUIRED'


def test_interrupted_settlement_recovers_once(system,monkeypatch):
    shared,ledger,router,q=system;router.set_enabled(True)
    router.submit(buy(),{'BTC/USD':q},now=NOW)
    original=shared.settle_crypto_flat
    def crash(*a,**k): raise RuntimeError('synthetic settlement interruption')
    monkeypatch.setattr(shared,'settle_crypto_flat',crash)
    later=NOW+timedelta(seconds=1);q=replace(q,timestamp=later,bid=D(97),ask=D(98))
    with pytest.raises(RuntimeError): router.protect({'BTC/USD':q},now=later)
    assert ledger.intent_evidence('one')['state']=='CLOSED' and shared.snapshot('pool')['active_commands']==1
    monkeypatch.setattr(shared,'settle_crypto_flat',original)
    assert list(router.reconcile().values())==['CLOSED']
    assert shared.snapshot('pool')['active_commands']==0 and router.reconcile()=={}


def test_wrong_pool_or_ledger_cannot_bind_and_unmanaged_positions_are_not_adopted(system,tmp_path):
    shared,ledger,_,q=system
    shared.configure_account('other',capital=D(1000),loss_budget=D(50))
    with pytest.raises(ValueError): CryptoAllocationRouter(shared,ledger,account_id='other',authorize=lambda *a:True)
    other=CryptoPaper(tmp_path/'other.sqlite3')
    try:
        with pytest.raises(ValueError): CryptoAllocationRouter(shared,other,account_id='pool',authorize=lambda *a:True)
    finally: other.close()
    standalone=CryptoPaper(tmp_path/'standalone.sqlite3');standalone.apply(buy(),{'BTC/USD':q},now=NOW)
    try:
        with pytest.raises(ValueError,match='existing crypto exposure'): CryptoAllocationRouter(shared,standalone,account_id='pool',authorize=lambda *a:True)
    finally: standalone.close()


def test_options_futures_crypto_compete_and_book_combined_results(tmp_path):
    from app.asset_modules.futures_allocation import FuturesAllocationRouter
    from app.asset_modules.futures_paper import FuturesPaper
    from app.asset_modules.options_allocation import OptionsAllocationRouter
    from app.asset_modules.options_paper import OptionsPaper
    from tests.asset_modules.test_futures_allocation import inputs as futures_inputs
    from tests.asset_modules.test_options_allocation import inputs as options_inputs
    shared=PaperAdmissionController(tmp_path/'s.sqlite3')
    shared.configure_account('pool',capital=D(3000),loss_budget=D(100))
    fl=FuturesPaper(tmp_path/'f.sqlite3');fl.set_enabled(True)
    ol=OptionsPaper(tmp_path/'o.sqlite3');ol.set_enabled(True)
    cl=CryptoPaper(tmp_path/'c.sqlite3')
    fr=FuturesAllocationRouter(shared,fl,account_id='pool')
    orouter=OptionsAllocationRouter(shared,ol,account_id='pool')
    cr=CryptoAllocationRouter(shared,cl,account_id='pool',authorize=lambda *a:True);cr.set_enabled(True)
    fc,_,fq,fp=futures_inputs();_,_,_,oqs,op=options_inputs();cq=replace(quote(),timestamp=NOW)
    try:
        assert fr.submit(fp,fq,now=NOW)=='OPENED'
        assert orouter.submit(op,oqs[op.plan.contract.symbol],now=NOW)=='OPENED'
        assert cr.submit(buy(),{'BTC/USD':cq},now=NOW)['entry']=='SHARED_CAPITAL_LIMIT'
        later=NOW+timedelta(seconds=1)
        fq=replace(fq,bid=fp.plan.stop,ask=fp.plan.stop+fc.tick,source_at=later,observed_at=later)
        fl.protect({fc.symbol:fq},now=later);fr.reconcile()
        oq=replace(oqs[op.plan.contract.symbol],bid=op.plan.stop,ask=op.plan.stop+D('.01'),source_at=later,observed_at=later)
        ol.protect({oq.symbol:oq},now=later);orouter.reconcile()
        cq=replace(cq,timestamp=later)
        assert cr.submit(buy(),{'BTC/USD':cq},now=later)['entry']=='OPENED'
        later+=timedelta(seconds=1);cq=replace(cq,timestamp=later,bid=D(97),ask=D(98))
        cr.protect({'BTC/USD':cq},now=later)
        balance=shared.allocation_balance('pool')
        pnl=cl.intent_evidence('one')['realized_pnl']
        assert balance['derivatives_realized_pnl']==D('-40.2')
        assert balance['simulator_realized_pnl']==D('-40.2')+pnl
        assert balance['capital_ceiling']==D(3000)+D('-40.2')+pnl
        assert shared.snapshot('pool')['active_commands']==0
    finally: fl.close();ol.close();cl.close()
