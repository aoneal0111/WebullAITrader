"""Isolated option quote fills, deterministic protection and durable accounting."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.engine_catalog import EngineId
from tests.asset_modules.test_contract_logic import contract, quote, make_plan, NOW


@pytest.fixture
def paper(tmp_path):
    p = OptionsPaper(tmp_path/'options.db')
    p.set_enabled(True)
    yield p
    p.close()


def enter(paper, identity='trade', p=None, q=None, now=NOW):
    return paper.enter(identity, p or make_plan(), q or quote(), now=now)


def test_activation_and_cash_fee_slippage_accounting(paper):
    paper.set_enabled(False)
    assert enter(paper) == 'ENTRIES_DISABLED'
    paper.set_enabled(True)
    assert enter(paper) == 'OPENED'
    s = paper.snapshot()
    assert D(s['cash']) == D('2089.40')
    assert s['positions'][0]['entry_fill'] == '1.02'
    assert D(s['positions'][0]['reserved_risk']) == D('21.20')
    paper.set_enabled(False)  # Disabling new entries cannot stop protection.
    later = NOW+timedelta(seconds=1)
    assert paper.protect({'QQQ_CALL': quote(bid='1.11', ask='1.12', at=later)}, now=later) == {'trade':'TARGET'}
    s = paper.snapshot()
    assert not s['positions'] and D(s['realized_pnl']) == D('26.80')
    assert D(s['cash']) == D(2500)+D(s['realized_pnl'])
    assert s['events'][0]['evidence'] == 'OPTION_QUOTE_PROXY_NOT_EXECUTABLE_FILL'
    assert enter(paper) == 'DUPLICATE'


@pytest.mark.parametrize('change,expected', [('stale','QUOTE_UNAVAILABLE'), ('future','QUOTE_UNAVAILABLE'),
    ('other','QUOTE_UNAVAILABLE'), ('price','REQUOTE_REQUIRED'), ('old_plan','PLAN_EXPIRED_OR_NOT_CURRENT'),
    ('tiny_target','NET_REWARD_INSUFFICIENT')])
def test_entry_rejections_leave_no_durable_fill(paper, change, expected):
    p, q = make_plan(), quote()
    if change == 'stale': q = quote(at=NOW-timedelta(seconds=3))
    elif change == 'future': q = quote(at=NOW+timedelta(seconds=1))
    elif change == 'other': q = quote(symbol='QQQ')
    elif change == 'price': q = quote(ask='1.02')
    elif change == 'old_plan': p = replace(p, created_at=NOW-timedelta(seconds=3), deadline=NOW+timedelta(seconds=57))
    else: p = replace(p, target=p.entry+D('0.05'))
    assert enter(paper,p=p,q=q) == expected
    s = paper.snapshot()
    assert not s['positions'] and not s['events'] and D(s['cash']) == 2500


def test_duplicate_identity_cannot_change_plan_or_reopen_closed_trade(paper):
    assert enter(paper) == 'OPENED'
    assert enter(paper) == 'DUPLICATE'
    with pytest.raises(ValueError, match='reused'):
        enter(paper,p=replace(make_plan(), quantity=3))
    assert len(paper.snapshot()['positions']) == 1


def test_call_and_put_share_underlying_ownership_and_other_underlying_can_open(paper):
    assert enter(paper) == 'OPENED'
    put = contract(symbol='QQQ_PUT',right='PUT')
    assert enter(paper,'put',make_plan(put,quote('QQQ_PUT')),quote('QQQ_PUT')) == 'UNDERLYING_ALREADY_OPEN'
    spy = contract(symbol='SPY_CALL',underlying='SPY')
    assert enter(paper,'spy',make_plan(spy,quote('SPY_CALL')),quote('SPY_CALL')) == 'OPENED'
    assert len(paper.snapshot()['positions']) == 2


@pytest.mark.parametrize('setting,expected', [('risk_per_trade','RISK_LIMIT'),('total_open_risk','RISK_LIMIT'),('starting_cash','CASH_LIMIT')])
def test_ledger_limits_override_a_native_plan(tmp_path, setting, expected):
    kwargs = {setting:D('10')}
    p = OptionsPaper(tmp_path/'limits.db',**kwargs)
    try:
        p.set_enabled(True)
        assert enter(p) == expected
        assert not p.snapshot()['positions']
    finally: p.close()


def test_missing_quote_does_not_fill_hold_or_expiry(paper):
    assert enter(paper) == 'OPENED'
    later = NOW+timedelta(seconds=61)
    assert paper.protect({},now=later) == {'trade':'AWAITING_FRESH_QUOTE'}
    assert paper.protect({'QQQ_CALL':quote()},now=later) == {'trade':'AWAITING_FRESH_QUOTE'}
    expires = make_plan().contract.expires_at
    assert paper.protect({'QQQ_CALL':quote(at=expires)},now=expires) == {'trade':'EXPIRY_REQUIRES_RECONCILIATION'}
    assert len(paper.snapshot()['positions']) == 1 and D(paper.snapshot()['realized_pnl']) == 0


@pytest.mark.parametrize('kind', ['STOP','MAX_HOLD'])
def test_exits_use_bid_and_gap_losses_remain_realized(paper,kind):
    assert enter(paper) == 'OPENED'
    later = NOW+timedelta(seconds=61 if kind=='MAX_HOLD' else 1)
    q = quote(bid='0.90' if kind=='STOP' else '1.00',ask='1.01',at=later)
    assert paper.protect({'QQQ_CALL':q},now=later) == {'trade':kind}
    assert D(paper.snapshot()['realized_pnl']) == (D('-57.20') if kind=='STOP' else D('-17.20'))
    assert paper.protect({'QQQ_CALL':q},now=later) == {}


def test_reopen_preserves_positions_and_intent_deduplication(tmp_path):
    path = tmp_path/'restart.db'
    p = OptionsPaper(path)
    p.set_enabled(True)
    assert enter(p) == 'OPENED'
    p.close()
    p = OptionsPaper(path)
    try:
        assert not p.enabled and len(p.snapshot()['positions']) == 1
        assert enter(p) == 'DUPLICATE'
        with pytest.raises(ValueError,match='settings conflict'):
            OptionsPaper(path,slippage=D('0.02'))
    finally: p.close()


def test_partial_transaction_failure_rolls_back_account_positions_and_intents(paper,monkeypatch):
    original = paper._event
    monkeypatch.setattr(paper,'_event',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('journal failure')))
    with pytest.raises(RuntimeError): enter(paper)
    assert not paper.snapshot()['positions'] and D(paper.snapshot()['cash']) == 2500
    monkeypatch.setattr(paper,'_event',original)
    assert enter(paper) == 'OPENED'


def test_concurrent_ledgers_cannot_double_fill_same_underlying(tmp_path):
    path = tmp_path/'concurrent.db'
    a,b = OptionsPaper(path),OptionsPaper(path)
    a.set_enabled(True);b.set_enabled(True)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda item: enter(item[0],item[1]), [(a,'a'),(b,'b')]))
        assert sorted(results) == ['OPENED','UNDERLYING_ALREADY_OPEN']
        assert len(a.snapshot()['positions']) == 1 and len(a.snapshot()['events']) == 1
    finally: a.close();b.close()


@pytest.mark.parametrize('change', ['fraction','short','futures','nan','target','hold'])
def test_forged_contract_plan_rejected_before_fill(paper,change):
    p = make_plan()
    if change=='fraction': p=replace(p,quantity=1.5)
    elif change=='short': p=replace(p,side='SHORT')
    elif change=='futures': p=replace(p,contract=contract(EngineId.FUTURES))
    elif change=='nan': p=replace(p,stop=D('NaN'))
    elif change=='target': p=replace(p,target=D('1.123'))
    else: p=replace(p,deadline=NOW+timedelta(minutes=2))
    with pytest.raises(ValueError): enter(paper,p=p)
    assert not paper.snapshot()['positions']


def test_protection_rejects_rewound_contract_quote(paper):
    assert enter(paper) == 'OPENED'
    later = NOW+timedelta(seconds=1)
    assert paper.protect({'QQQ_CALL':quote(at=later)},now=later) == {'trade':'HELD'}
    assert paper.protect({'QQQ_CALL':quote(bid='0.90',at=NOW)},now=later) == {'trade':'AWAITING_FRESH_QUOTE'}
    assert len(paper.snapshot()['positions']) == 1


def test_exit_journal_failure_preserves_position_and_cash(paper,monkeypatch):
    assert enter(paper) == 'OPENED'
    before = paper.snapshot()
    original = paper._event
    monkeypatch.setattr(paper,'_event',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('journal failure')))
    later = NOW+timedelta(seconds=1)
    quotes = {'QQQ_CALL':quote(bid='1.11',ask='1.12',at=later)}
    with pytest.raises(RuntimeError): paper.protect(quotes,now=later)
    assert paper.snapshot() == before
    monkeypatch.setattr(paper,'_event',original)
    assert paper.protect(quotes,now=later) == {'trade':'TARGET'}


def test_unrelated_database_is_rejected_without_modification(tmp_path):
    import sqlite3
    path = tmp_path/'existing.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE existing (value TEXT)')
        db.execute("INSERT INTO existing VALUES ('preserve')")
    before = path.read_bytes()
    with pytest.raises(ValueError,match='dedicated Options'):
        OptionsPaper(path)
    assert path.read_bytes() == before
