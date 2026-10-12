"""Futures margin, tick P&L, durable ownership and symmetric long/short exits."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.asset_modules.contract_logic import Contract, Quote, plan
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.futures_paper import FuturesPaper
from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.contract_serialization import encode,decode

NOW = datetime(2026,10,12,15,tzinfo=UTC)


def quote(symbol='SYNTHETIC_MICRO',bid='5000',ask='5000.25',at=NOW):
    return Quote(symbol,D(bid),D(ask),at,at)


def prepared(side='LONG',symbol='SYNTHETIC_MICRO'):
    c=Contract(symbol,EngineId.FUTURES,NOW+timedelta(days=30),D(5),D('0.25'),D(1000))
    return plan(c,quote(symbol),NOW,side=side,stop_distance=D(1),target_distance=D(3),
        risk_budget=D(25),capital_budget=D(2500),fee_per_side=D(1),hold=timedelta(seconds=60))


@pytest.fixture
def paper(tmp_path):
    p=FuturesPaper(tmp_path/'futures.db')
    p.set_enabled(True)
    yield p
    p.close()


def enter(paper,p=None,identity='one',q=None,now=NOW):
    return paper.enter(identity,p or prepared(),q or quote(),now=now)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_margin_is_reserved_without_debiting_notional_and_targets_are_symmetric(paper,side):
    p=prepared(side)
    assert p.quantity==2 and enter(paper,p)== 'OPENED'
    s=paper.snapshot()
    assert D(s['cash'])==9998 and D(s['reserved_margin'])==2000
    assert D(s['margin_headroom'])==7996 and D(s['positions'][0]['reserved_risk'])==19
    paper.set_enabled(False)
    later=NOW+timedelta(seconds=1)
    q=quote(bid='5003.25' if side=='LONG' else '4996.75',ask='5003.50' if side=='LONG' else '4997',at=later)
    assert paper.protect({q.symbol:q},now=later)=={'one':'TARGET'}
    s=paper.snapshot()
    assert not s['positions'] and D(s['reserved_margin'])==0
    assert D(s['realized_pnl'])==21 and D(s['cash'])==10021
    assert s['events'][0]['evidence']=='FUTURES_QUOTE_PROXY_NOT_EXECUTABLE_FILL'
    assert enter(paper,p)=='DUPLICATE'


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('kind',['STOP','GAP','MAX_HOLD'])
def test_bid_long_and_ask_short_exit_costs_and_gap_losses(paper,side,kind):
    assert enter(paper,prepared(side))=='OPENED'
    later=NOW+timedelta(seconds=61 if kind=='MAX_HOLD' else 1)
    if kind=='MAX_HOLD':q=quote(at=later)
    elif side=='LONG':q=quote(bid='4999.25' if kind=='STOP' else '4998.25',ask='4999.50',at=later)
    else:q=quote(bid='5000.75',ask='5001' if kind=='STOP' else '5002',at=later)
    assert paper.protect({q.symbol:q},now=later)=={'one':'MAX_HOLD' if kind=='MAX_HOLD' else 'STOP'}
    expected={'STOP':D(-19),'GAP':D(-29),'MAX_HOLD':D('-11.50')}[kind]
    assert D(paper.snapshot()['realized_pnl'])==expected
    assert D(paper.snapshot()['cash'])==D(10000)+expected
    assert paper.protect({q.symbol:q},now=later)=={}


@pytest.mark.parametrize('setting,value,expected', [('starting_cash',D(1500),'MARGIN_LIMIT'),
    ('risk_per_trade',D(10),'RISK_LIMIT'),('total_open_risk',D(10),'RISK_LIMIT')])
def test_monetary_caps_apply_before_entry(tmp_path,setting,value,expected):
    p=FuturesPaper(tmp_path/'limits.db',**{setting:value})
    try:
        p.set_enabled(True)
        assert enter(p)==expected
        assert not p.snapshot()['positions'] and not p.snapshot()['events']
    finally:p.close()


def test_ownership_aggregate_margin_and_position_budget(paper):
    assert enter(paper)=='OPENED'
    assert enter(paper,identity='duplicate')=='CONTRACT_ALREADY_OPEN'
    assert enter(paper,prepared(symbol='SECOND'),identity='two',q=quote('SECOND'))=='OPENED'
    assert D(paper.snapshot()['reserved_margin'])==4000
    assert enter(paper,prepared(symbol='THIRD'),identity='three',q=quote('THIRD'))=='POSITION_LIMIT'
    with pytest.raises(ValueError,match='reused'):enter(paper,prepared('SHORT'))


@pytest.mark.parametrize('kind',['stale','future','wrong_symbol','bad_tick','requote','old_plan'])
def test_invalid_quote_or_old_plan_does_not_create_state(paper,kind):
    p,q=prepared(),quote()
    expected='QUOTE_UNAVAILABLE'
    if kind=='stale':q=quote(at=NOW-timedelta(seconds=3))
    elif kind=='future':q=quote(at=NOW+timedelta(seconds=1))
    elif kind=='wrong_symbol':q=quote('OTHER')
    elif kind=='bad_tick':q=quote(bid='5000.01')
    elif kind=='requote':q=quote(ask='5000.50');expected='REQUOTE_REQUIRED'
    else:p=replace(p,created_at=NOW-timedelta(seconds=3));expected='PLAN_EXPIRED_OR_NOT_CURRENT'
    assert enter(paper,p,q=q)==expected
    assert not paper.snapshot()['events']


def test_missing_stale_rewound_quotes_and_expiry_do_not_invent_exit(paper):
    p=prepared()
    assert enter(paper,p)=='OPENED'
    later=NOW+timedelta(seconds=1)
    assert paper.protect({quote().symbol:quote(at=later)},now=later)=={'one':'HELD'}
    assert paper.protect({quote().symbol:quote(bid='4990')},now=later)=={'one':'AWAITING_FRESH_QUOTE'}
    assert paper.protect({},now=NOW+timedelta(minutes=2))=={'one':'AWAITING_FRESH_QUOTE'}
    expires=p.contract.expires_at
    assert paper.protect({quote().symbol:quote(at=expires)},now=expires)=={'one':'EXPIRY_REQUIRES_RECONCILIATION'}
    assert len(paper.snapshot()['positions'])==1 and D(paper.snapshot()['realized_pnl'])==0


def test_restart_preserves_none_strike_margin_and_intent(tmp_path):
    path=tmp_path/'restart.db'
    p=FuturesPaper(path);p.set_enabled(True)
    assert enter(p)=='OPENED'
    assert decode(encode(prepared())).contract.strike is None
    p.close();p=FuturesPaper(path)
    try:
        assert not p.enabled and D(p.snapshot()['reserved_margin'])==2000
        assert enter(p)=='DUPLICATE'
        with pytest.raises(ValueError,match='settings conflict'):FuturesPaper(path,slippage_ticks=2)
    finally:p.close()


@pytest.mark.parametrize('kind',['entry','exit'])
def test_failed_journal_transaction_rolls_back_positions_and_money(paper,monkeypatch,kind):
    if kind=='exit':assert enter(paper)=='OPENED'
    before=paper.snapshot()
    original=paper._event
    monkeypatch.setattr(paper,'_event',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('journal unavailable')))
    later=NOW+timedelta(seconds=1)
    with pytest.raises(RuntimeError):
        if kind=='entry':enter(paper)
        else:paper.protect({quote().symbol:quote(bid='5003.25',ask='5003.50',at=later)},now=later)
    assert paper.snapshot()==before
    monkeypatch.setattr(paper,'_event',original)
    if kind=='entry':assert enter(paper)=='OPENED'
    else:assert paper.protect({quote().symbol:quote(bid='5003.25',ask='5003.50',at=later)},now=later)=={'one':'TARGET'}


def test_competing_connections_cannot_double_fill_same_contract(tmp_path):
    path=tmp_path/'concurrent.db'
    a,b=FuturesPaper(path),FuturesPaper(path)
    a.set_enabled(True);b.set_enabled(True)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda item:enter(item[0],identity=item[1]),[(a,'a'),(b,'b')]))
        assert sorted(results)==['CONTRACT_ALREADY_OPEN','OPENED']
        assert len(a.snapshot()['events'])==1
    finally:a.close();b.close()


def test_options_ledger_cannot_be_opened_as_futures_or_overwritten(tmp_path):
    path=tmp_path/'options.db'
    p=OptionsPaper(path);p.close()
    before=path.read_bytes()
    with pytest.raises(ValueError,match='dedicated Futures'):FuturesPaper(path)
    assert path.read_bytes()==before


@pytest.mark.parametrize('kind',['fraction','side','tick','nan','options','hold','target'])
def test_forged_plans_are_rejected_before_fill(paper,kind):
    p=prepared()
    if kind=='fraction':p=replace(p,quantity=1.5)
    elif kind=='side':p=replace(p,side='BUY')
    elif kind=='tick':p=replace(p,stop=D('4999.10'))
    elif kind=='nan':p=replace(p,stop=D('NaN'))
    elif kind=='options':
        from tests.asset_modules.test_contract_logic import make_plan
        p=make_plan()
    elif kind=='hold':p=replace(p,deadline=NOW+timedelta(hours=2))
    else:p=replace(p,target=p.entry-D(1))
    with pytest.raises(ValueError):enter(paper,p)
    assert not paper.snapshot()['positions']


def test_future_realized_profit_cannot_fund_an_earlier_replayed_entry(paper):
    assert enter(paper)=='OPENED'
    later=NOW+timedelta(seconds=1)
    q=quote(bid='5003.25',ask='5003.50',at=later)
    assert paper.protect({q.symbol:q},now=later)=={'one':'TARGET'}
    assert enter(paper,prepared('SHORT'),identity='backdated')=='OUT_OF_ORDER_EVENT'
    current=NOW+timedelta(seconds=2)
    p=replace(prepared('SHORT'),created_at=current,deadline=current+timedelta(seconds=60))
    assert enter(paper,p,identity='current',q=quote(at=current),now=current)=='OPENED'
    assert paper.protect({q.symbol:q},now=later)=={'current':'OUT_OF_ORDER_EVENT'}
