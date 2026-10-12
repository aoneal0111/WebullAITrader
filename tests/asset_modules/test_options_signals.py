from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.asset_modules.contract_logic import Bar, Contract, Quote
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.options_signals import OptionsHistory, OptionsPaperController, OptionsSignalSettings, propose

NOW = datetime(2026, 10, 12, 15, tzinfo=UTC)
SETTINGS = OptionsSignalSettings()


def data(side='LONG', underlying='SPY'):
    bars = tuple(Bar(NOW-timedelta(minutes=21-i), D(100), D('99.75'), D(100)) for i in range(21))
    latest = Bar(NOW, D('100.25'), D(100), D('100.25'))
    uq = Quote(underlying, D('100.25'), D('100.26'), NOW, NOW)
    if side == 'SHORT':
        bars = tuple(Bar(b.end, D('100.25'), D(100), D(100)) for b in bars)
        latest = Bar(NOW, D(100), D('99.75'), D('99.75'))
        uq = Quote(underlying, D('99.74'), D('99.75'), NOW, NOW)
    contracts = tuple(Contract(underlying+'_'+right, EngineId.OPTIONS, NOW+timedelta(days=30),
        D(100), D('.01'), D(1), underlying, right, D(100)) for right in ('CALL', 'PUT'))
    quotes = {c.symbol: Quote(c.symbol, D('1.99'), D('2.00'), NOW, NOW) for c in contracts}
    return OptionsHistory(underlying, bars+(latest,)), uq, contracts, quotes


@pytest.mark.parametrize('side,right', [('LONG', 'CALL'), ('SHORT', 'PUT')])
@pytest.mark.parametrize('underlying', ['SPY', 'QQQ'])
def test_direction_selects_long_premium_and_cost_inclusive_size(side, right, underlying):
    h, uq, cs, qs = data(side, underlying)
    candidates, reason = propose(h, uq, cs, qs, NOW, SETTINGS)
    assert reason == 'PROPOSED' and len(candidates) == 1
    p = candidates[0].plan
    assert p.contract.right == right and p.side == 'LONG' and p.quantity == 4
    assert p.stop == D('1.98') and p.target == D('2.10')
    assert p.quantity*D('5.30') <= SETTINGS.risk_budget
    assert p.quantity*D('202.30') <= SETTINGS.capital_budget


@pytest.mark.parametrize('case,reason', [
    ('few', 'INSUFFICIENT_HISTORY'), ('gap', 'HISTORY_NOT_CURRENT_OR_CONTIGUOUS'),
    ('stale_history', 'HISTORY_NOT_CURRENT_OR_CONTIGUOUS'), ('flat', 'NO_CONFIRMED_BREAKOUT'),
    ('stale_underlying', 'UNDERLYING_QUOTE_UNAVAILABLE'), ('wrong_underlying', 'UNDERLYING_QUOTE_UNAVAILABLE'),
    ('old_underlying', 'UNDERLYING_QUOTE_UNAVAILABLE'), ('unconfirmed', 'TRIGGER_NOT_CURRENT'),
    ('missing_premium', 'NO_ELIGIBLE_PREMIUM_PLAN'), ('stale_premium', 'NO_ELIGIBLE_PREMIUM_PLAN'),
    ('wrong_premium', 'NO_ELIGIBLE_PREMIUM_PLAN'), ('expiry', 'NO_ELIGIBLE_PREMIUM_PLAN'),
    ('tick', 'NO_ELIGIBLE_PREMIUM_PLAN'), ('budget', 'NO_ELIGIBLE_PREMIUM_PLAN'),
    ('fees', 'NO_ELIGIBLE_PREMIUM_PLAN'), ('small_target', 'NO_ELIGIBLE_PREMIUM_PLAN')])
def test_missing_or_unexecutable_data_cannot_propose(case, reason):
    h, uq, cs, qs = data()
    now, settings = NOW, SETTINGS
    if case == 'few': h = replace(h, bars=h.bars[:10])
    if case == 'gap': h = replace(h, bars=(replace(h.bars[0], end=h.bars[0].end-timedelta(minutes=1)),)+h.bars[1:])
    if case == 'stale_history': now += timedelta(seconds=61)
    if case == 'flat': h = replace(h, bars=h.bars[:-1]+(Bar(NOW, D(100), D('99.75'), D(100)),))
    if case == 'stale_underlying': uq = replace(uq, source_at=NOW-timedelta(seconds=3))
    if case == 'wrong_underlying': uq = replace(uq, symbol='QQQ')
    if case == 'old_underlying': uq = replace(uq, source_at=NOW-timedelta(seconds=1))
    if case == 'unconfirmed': uq = replace(uq, bid=D(100))
    if case == 'missing_premium': qs = {}
    if case == 'stale_premium': qs = {s:replace(q, source_at=NOW-timedelta(seconds=3)) for s,q in qs.items()}
    if case == 'wrong_premium': qs = {s:replace(q, symbol='OTHER') for s,q in qs.items()}
    if case == 'expiry': cs = tuple(replace(c, expires_at=NOW+SETTINGS.hold) for c in cs)
    if case == 'tick': qs = {s:replace(q, ask=D('2.001')) for s,q in qs.items()}
    if case == 'budget': settings = replace(settings, risk_budget=D(1))
    if case == 'fees': settings = replace(settings, fee_per_side=D(10))
    if case == 'small_target': settings = replace(settings, target_distance=D('.05'))
    assert propose(h, uq, cs, qs, now, settings) == ((), reason)


@pytest.mark.parametrize('case', ['future', 'reverse', 'list', 'oversized', 'duplicates', 'wrong_history'])
def test_invalid_input_rejected(case):
    h, uq, cs, qs = data()
    if case == 'future': h = replace(h, bars=h.bars[:-1]+(replace(h.bars[-1], end=NOW+timedelta(seconds=1)),))
    if case == 'reverse': h = replace(h, bars=tuple(reversed(h.bars)))
    if case == 'list': h = replace(h, bars=list(h.bars))
    if case == 'oversized': h = replace(h, bars=h.bars*12)
    if case == 'duplicates': cs = cs+cs
    if case == 'wrong_history': h = replace(h, underlying='AAPL')
    with pytest.raises(ValueError): propose(h, uq, cs, qs, NOW, SETTINGS)


def test_ranking_submits_only_one_alternative_and_dedup_survives_restart(tmp_path):
    h, uq, cs, qs = data()
    extra = replace(cs[0], symbol='SPY_CALL_SECOND', strike=D(101))
    cs += (extra,)
    qs[extra.symbol] = Quote(extra.symbol, D('1.98'), D(2), NOW, NOW)
    path = tmp_path/'options.sqlite3'
    ledger = OptionsPaper(path)
    calls = []
    controller = OptionsPaperController(ledger, settings=SETTINGS, authorize=lambda p,n: calls.append(p) or True)
    assert controller.step(h,uq,cs,qs,now=NOW)['entry'] == 'ENTRIES_DISABLED'
    assert not calls
    ledger.set_enabled(True)
    opened = controller.step(h,uq,cs,qs,now=NOW)
    assert opened['entry'] == 'OPENED' and len(calls) == 1
    p = opened['proposal'].plan
    assert p.contract.symbol == 'SPY_CALL'
    ledger.close()
    ledger = OptionsPaper(path)
    controller = OptionsPaperController(ledger, settings=SETTINGS, authorize=lambda *a: pytest.fail('Exit must not need entry authorization'))
    later = NOW+timedelta(seconds=1)
    qs[p.contract.symbol] = Quote(p.contract.symbol, p.target, p.target+p.contract.tick, later, later)
    closed = controller.step(h,replace(uq, source_at=later, observed_at=later),cs,qs,now=later)
    assert closed['protection'][opened['proposal'].intent_id] == 'TARGET'
    ledger.set_enabled(True)
    # Remove original strike: the same underlying/bar signal cannot buy another alternative.
    cs = (extra,)
    qs[extra.symbol] = replace(qs[extra.symbol], source_at=later, observed_at=later)
    assert controller.step(h,replace(uq, source_at=later, observed_at=later),cs,qs,now=later)['entry'] == 'DUPLICATE_SIGNAL'
    assert not ledger.snapshot()['positions']
    ledger.close()


def test_strict_authorization_and_final_ledger_limits(tmp_path):
    h, uq, cs, qs = data()
    ledger = OptionsPaper(tmp_path/'o.sqlite3', risk_per_trade=D(5))
    ledger.set_enabled(True)
    controller = OptionsPaperController(ledger, settings=SETTINGS, authorize=lambda *a: 1)
    assert controller.step(h,uq,cs,qs,now=NOW)['entry'] == 'ENTRY_NOT_AUTHORIZED'
    controller = OptionsPaperController(ledger, settings=SETTINGS, authorize=lambda *a: True)
    assert controller.step(h,uq,cs,qs,now=NOW)['entry'] == 'RISK_LIMIT'
    assert not ledger.snapshot()['events']
    with pytest.raises(ValueError): OptionsPaperController(ledger, settings=replace(SETTINGS, slippage=D('.02')), authorize=lambda *a: True)
    ledger.close()


@pytest.mark.parametrize('changes', [{'risk_budget':D('NaN')}, {'hold':timedelta(seconds=61)},
    {'max_contracts':6}, {'max_contracts':True}, {'target_distance':D('.20')}, {'slippage':D('-1')}])
def test_invalid_settings(changes):
    with pytest.raises(ValueError): replace(SETTINGS, **changes)


def test_existing_protection_survives_missing_underlying_and_entry_refusal(tmp_path):
    h, uq, cs, qs = data()
    ledger = OptionsPaper(tmp_path/'o.sqlite3')
    ledger.set_enabled(True)
    controller = OptionsPaperController(ledger, settings=SETTINGS, authorize=lambda *a: True)
    opened = controller.step(h,uq,cs,qs,now=NOW)
    p = opened['proposal'].plan
    ledger.set_enabled(False)
    controller.authorize = lambda *a: pytest.fail('Protection cannot call entry authorization')
    later = NOW+timedelta(seconds=1)
    missing = controller.step(OptionsHistory('SPY',()),None,(),{},now=later)
    assert missing['protection'][opened['proposal'].intent_id] == 'AWAITING_FRESH_QUOTE'
    q = Quote(p.contract.symbol,p.stop,p.stop+p.contract.tick,later,later)
    stopped = controller.step(OptionsHistory('SPY',()),None,(),{p.contract.symbol:q},now=later)
    assert stopped['protection'][opened['proposal'].intent_id] == 'STOP'
    assert not ledger.snapshot()['positions']
    ledger.close()
