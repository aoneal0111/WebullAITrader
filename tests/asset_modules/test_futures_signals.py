from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.asset_modules.contract_logic import Bar, Contract, Quote
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.futures_paper import FuturesPaper
from app.asset_modules.futures_signals import (
    FuturesHistory, FuturesPaperController, FuturesSignalSettings, propose,
)

NOW = datetime(2026, 10, 12, 15, tzinfo=UTC)
SETTINGS = FuturesSignalSettings()


def data(side='LONG', now=NOW):
    c = Contract('SYNTHETIC_MICRO', EngineId.FUTURES, NOW+timedelta(days=30), D(5), D('.25'), D(1000))
    # Flat prior range, then a completed breakout with EMA agreement.
    old = tuple(Bar(now-timedelta(minutes=21-i), D(100), D('99.75'), D(100)) for i in range(21))
    latest = Bar(now, D('100.25'), D(100), D('100.25'))
    q = Quote(c.symbol, D('100.25'), D('100.50'), now, now)
    if side == 'SHORT':
        old = tuple(Bar(b.end, D('100.25'), D(100), D(100)) for b in old)
        latest = Bar(now, D(100), D('99.75'), D('99.75'))
        q = Quote(c.symbol, D('99.50'), D('99.75'), now, now)
    return c, FuturesHistory(c.symbol, old+(latest,)), q


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_symmetric_structural_plans_and_cost_inclusive_sizing(side):
    c, h, q = data(side)
    candidate, reason = propose(c, h, q, NOW, SETTINGS)
    assert reason == 'PROPOSED'
    p = candidate.plan
    assert p.side == side and p.quantity == 2
    assert p.stop == (D('99.50') if side == 'LONG' else D('100.50'))
    assert p.target == (D('103.50') if side == 'LONG' else D('96.50'))
    assert p.quantity*(D('1.50')*c.multiplier+2*SETTINGS.fee_per_side) <= SETTINGS.risk_budget
    assert p.quantity*(c.margin+2*SETTINGS.fee_per_side) <= SETTINGS.capital_budget
    later = NOW+timedelta(milliseconds=500)
    updated, _ = propose(c, h, replace(q, source_at=later, observed_at=later), later, SETTINGS)
    assert updated.intent_id == candidate.intent_id
    assert updated.plan.created_at != p.created_at


@pytest.mark.parametrize('case,reason', [
    ('short_history', 'INSUFFICIENT_HISTORY'), ('gap', 'HISTORY_NOT_CURRENT_OR_CONTIGUOUS'),
    ('stale_history', 'HISTORY_NOT_CURRENT_OR_CONTIGUOUS'), ('tick', 'BAR_TICK_MISMATCH'),
    ('flat', 'NO_CONFIRMED_BREAKOUT'), ('stale_quote', 'QUOTE_UNAVAILABLE'),
    ('future_quote', 'QUOTE_UNAVAILABLE'), ('wrong_quote', 'QUOTE_UNAVAILABLE'),
    ('old_quote', 'QUOTE_UNAVAILABLE'), ('unconfirmed', 'TRIGGER_NOT_CURRENT'),
    ('extended', 'ENTRY_EXTENDED'), ('expiry', 'CONTRACT_EXPIRING'),
    ('budget', 'BUDGET_INSUFFICIENT'), ('fees', 'NET_REWARD_INSUFFICIENT'),
])
def test_missing_stale_extended_or_unaffordable_inputs_do_not_create_entries(case, reason):
    c, h, q = data()
    settings, now = SETTINGS, NOW
    if case == 'short_history': h = replace(h, bars=h.bars[:10])
    if case == 'gap': h = replace(h, bars=h.bars[:5]+h.bars[6:]+(replace(h.bars[-1], end=NOW+timedelta(minutes=1)),)); now += timedelta(minutes=1)
    if case == 'stale_history': now += timedelta(seconds=61); q = replace(q, source_at=now, observed_at=now)
    if case == 'tick': h = replace(h, bars=h.bars[:-1]+(replace(h.bars[-1], high=D('100.30'), close=D('100.30')),))
    if case == 'flat': h = replace(h, bars=h.bars[:-1]+(Bar(NOW, D(100), D('99.75'), D(100)),))
    if case == 'stale_quote': q = replace(q, source_at=NOW-timedelta(seconds=3))
    if case == 'future_quote': q = replace(q, source_at=NOW+timedelta(seconds=1), observed_at=NOW+timedelta(seconds=1))
    if case == 'wrong_quote': q = replace(q, symbol='OTHER')
    if case == 'old_quote': q = replace(q, source_at=NOW-timedelta(seconds=1))
    if case == 'unconfirmed': q = replace(q, bid=D(100), ask=D('100.25'))
    if case == 'extended': q = replace(q, bid=D(101), ask=D('101.25'))
    if case == 'expiry': c = replace(c, expires_at=NOW+SETTINGS.hold)
    if case == 'budget': settings = replace(settings, risk_budget=D(1))
    if case == 'fees': settings = replace(settings, fee_per_side=D(10))
    assert propose(c, h, q, now, settings) == (None, reason)


@pytest.mark.parametrize('case', ['wrong_history', 'future_bar', 'reversed', 'list', 'oversized', 'wrong_engine'])
def test_invalid_identity_or_history_cannot_be_used(case):
    c, h, q = data()
    if case == 'wrong_history': h = replace(h, symbol='OTHER')
    if case == 'future_bar': h = replace(h, bars=h.bars[:-1]+(replace(h.bars[-1], end=NOW+timedelta(seconds=1)),))
    if case == 'reversed': h = replace(h, bars=tuple(reversed(h.bars)))
    if case == 'list': h = replace(h, bars=list(h.bars))
    if case == 'oversized': h = replace(h, bars=h.bars*12)
    if case == 'wrong_engine': c = Contract('QQQ_CALL', EngineId.OPTIONS, c.expires_at, D(100), D('.01'), D(1000), 'QQQ', 'CALL', D(100))
    with pytest.raises(ValueError): propose(c, h, q, NOW, SETTINGS)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_controller_authorization_protection_and_restart_dedup(tmp_path, side):
    path = tmp_path/'futures.sqlite3'
    ledger = FuturesPaper(path)
    calls = []
    controller = FuturesPaperController(ledger, settings=SETTINGS, authorize=lambda p, now: calls.append(p) or True)
    c, h, q = data(side)
    assert controller.step(c, h, {c.symbol:q}, now=NOW)['entry'] == 'ENTRIES_DISABLED'
    assert calls == []
    ledger.set_enabled(True)
    opened = controller.step(c, h, {c.symbol:q}, now=NOW)
    assert opened['entry'] == 'OPENED'
    identity = opened['proposal'].intent_id
    assert ledger.intent_state(identity) == 'OPEN'
    ledger.close()
    ledger = FuturesPaper(path)
    controller = FuturesPaperController(ledger, settings=SETTINGS, authorize=lambda *args: False)
    p = opened['proposal'].plan
    later = NOW+timedelta(seconds=1)
    exit_quote = Quote(c.symbol, p.target, p.target+c.tick, later, later) if side == 'LONG' else Quote(c.symbol, p.target-c.tick, p.target, later, later)
    # Closing is independent of default-off entries and a refusing callback.
    result = controller.step(c, h, {c.symbol:exit_quote}, now=later)
    assert result['protection'][identity] == 'TARGET'
    assert ledger.intent_state(identity) == 'CLOSED'
    ledger.set_enabled(True)
    later += timedelta(seconds=1)
    q = replace(q, source_at=later, observed_at=later)
    assert controller.step(c, h, {c.symbol:q}, now=later)['entry'] == 'DUPLICATE_SIGNAL'
    assert not ledger.snapshot()['positions']
    ledger.close()


def test_authorization_and_ledger_limits_are_both_required(tmp_path):
    ledger = FuturesPaper(tmp_path/'f.sqlite3', risk_per_trade=D(5))
    ledger.set_enabled(True)
    c, h, q = data()
    controller = FuturesPaperController(ledger, settings=SETTINGS, authorize=lambda *a: 1)
    assert controller.step(c,h,{c.symbol:q},now=NOW)['entry'] == 'ENTRY_NOT_AUTHORIZED'
    controller = FuturesPaperController(ledger, settings=SETTINGS, authorize=lambda *a: True)
    assert controller.step(c,h,{c.symbol:q},now=NOW)['entry'] == 'RISK_LIMIT'
    assert not ledger.snapshot()['events']
    with pytest.raises(ValueError): FuturesPaperController(ledger, settings=replace(SETTINGS, slippage_ticks=2), authorize=lambda *a: True)
    ledger.close()


@pytest.mark.parametrize('changes', [{'risk_budget':D('NaN')}, {'hold':timedelta(hours=2)}, {'max_contracts':6}, {'slippage_ticks':True}, {'target_r':D(1)}, {'max_extension_r':D(2)}])
def test_settings_are_bounded(changes):
    with pytest.raises(ValueError): replace(SETTINGS, **changes)
