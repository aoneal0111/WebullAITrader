from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import time

import pytest

from app.asset_modules.engine_worker import EngineWorker, WorkerState
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.options_signals import OptionsPaperController
from app.asset_modules.options_worker import OptionsSignalWorker, OptionsWorkerController, _assess, _pack
from tests.asset_modules.test_options_signals import NOW, SETTINGS, data


def wait(worker):
    until = time.monotonic()+10
    while time.monotonic() < until:
        reply = worker.poll()
        if reply: return reply
        if worker.state == WorkerState.FAILED: raise AssertionError(worker.status())
        time.sleep(.01)
    raise AssertionError('Worker did not reply')


@pytest.fixture
def worker():
    w = OptionsSignalWorker(timeout_seconds=10)
    w.start()
    assert wait(w)['kind'] == 'READY'
    try:
        yield w
    finally:
        w.close()


@pytest.mark.parametrize('side,right', [('LONG','CALL'),('SHORT','PUT')])
def test_real_subprocess_returns_exact_ranked_proposals(worker,side,right):
    h,uq,cs,qs = data(side)
    raw = _pack(h,uq,cs,qs,NOW,SETTINGS)
    assert worker.assess(h,uq,cs,qs,NOW,SETTINGS)
    with pytest.raises(RuntimeError): worker.assess(h,uq,cs,qs,NOW,SETTINGS)
    reply = wait(worker)
    assert reply['payload'] == _assess(raw)
    assert reply['payload']['proposals'][0]['plan']['contract']['right'] == right
    assert reply['payload']['proposals'][0]['plan']['side'] == 'LONG'
    assert worker.status()['execution_enabled'] is False
    worker.submit('PING')
    assert wait(worker)['payload']['pid'] == worker.pid


@pytest.mark.parametrize('case', ['extra','symbol','decimal','nonfinite','number','timestamp',
    'bars','bool_hold','future_bar','code','contract_count','wrong_quote','duplicate_contract','underlying'])
def test_malformed_requests_rejected_before_send(worker,case):
    h,uq,cs,qs = data()
    raw = _pack(h,uq,cs,qs,NOW,SETTINGS)
    if case == 'extra': raw['path'] = 'ledger.sqlite3'
    if case == 'symbol': raw['contracts'][0]['symbol'] = 'bad symbol'
    if case == 'decimal': raw['contracts'][0]['strike'] = 'oops'
    if case == 'nonfinite': raw['underlying_quote']['bid'] = 'NaN'
    if case == 'number': raw['contracts'][0]['tick'] = .01
    if case == 'timestamp': raw['at'] = '2026-10-12T15:00:00'
    if case == 'bars': raw['bars'] *= 3
    if case == 'bool_hold': raw['settings']['hold_microseconds'] = True
    if case == 'future_bar': raw['bars'][-1]['end'] = (NOW+timedelta(seconds=1)).isoformat()
    if case == 'code': raw['settings']['python_code'] = 'anything'
    if case == 'contract_count': raw['contracts'] *= 5
    if case == 'wrong_quote': raw['quotes'][cs[0].symbol]['symbol'] = cs[1].symbol
    if case == 'duplicate_contract': raw['contracts'] += raw['contracts']
    if case == 'underlying': raw['underlying'] = 'AAPL'
    with pytest.raises(ValueError): worker.submit('ASSESS',raw)
    assert worker.state == WorkerState.READY
    worker.submit('PING')
    assert wait(worker)['kind'] == 'PONG'


def test_generic_worker_cannot_assess_options():
    w = EngineWorker(EngineId.OPTIONS,policy_version='OBSERVATION_TRANSPORT_V1')
    h,uq,cs,qs = data()
    with pytest.raises(ValueError): w._request('ASSESS',_pack(h,uq,cs,qs,NOW,SETTINGS))
    w.close()


def test_response_cannot_change_price_type_or_ranking():
    h,uq,cs,qs = data()
    second = replace(cs[0],symbol='SPY_CALL_SECOND')
    cs += (second,)
    qs[second.symbol] = replace(qs[cs[0].symbol],symbol=second.symbol,bid=D('1.98'))
    w = OptionsSignalWorker()
    w._expected = 'ASSESSED'
    w._expected_payload = _pack(h,uq,cs,qs,NOW,SETTINGS)
    response = _assess(w._expected_payload)
    assert w._valid_payload(response)
    response['proposals'].reverse()
    assert not w._valid_payload(response)
    response = _assess(w._expected_payload)
    response['proposals'][0]['plan']['stop'] = '1'
    assert not w._valid_payload(response)
    w._expected_payload['settings']['max_contracts'] = 1
    response = _assess(w._expected_payload)
    response['proposals'][0]['plan']['quantity'] = True
    assert not w._valid_payload(response)
    w.close()


def bridge(ledger,worker,authorize=lambda *a:True):
    return OptionsWorkerController(worker,OptionsPaperController(ledger,settings=SETTINGS,authorize=authorize))


def drain(b,uq,qs,now):
    until = time.monotonic()+10
    while time.monotonic() < until:
        result = b.poll(uq,qs,now=now)
        if result['entry'] != 'PENDING_WORKER': return result
        time.sleep(.01)
    raise AssertionError('Bridge did not complete')


def test_parent_reprices_and_protects_after_worker_closes(worker,tmp_path):
    ledger = OptionsPaper(tmp_path/'o.sqlite3')
    b = bridge(ledger,worker)
    h,uq,cs,qs = data()
    ledger.set_enabled(True)
    assert b.submit(h,uq,cs,qs,now=NOW)['entry'] == 'PENDING_WORKER'
    assert b.submit(h,uq,cs,qs,now=NOW)['entry'] == 'WORKER_NOT_READY'
    later = NOW+timedelta(seconds=1)
    current = {s:replace(q,bid=D('2.09'),ask=D('2.10'),source_at=later,observed_at=later) for s,q in qs.items()}
    uq = replace(uq,source_at=later,observed_at=later)
    result = drain(b,uq,current,later)
    assert result['entry'] == 'OPENED'
    p = result['proposal'].plan
    assert p.entry == D('2.10')
    assert ledger.snapshot()['positions'][0]['entry_fill'] == '2.11'
    identity = result['proposal'].intent_id
    b.close()
    assert not ledger.enabled
    later += timedelta(seconds=1)
    exit_q = replace(current[p.contract.symbol],bid=p.target,ask=p.target+p.contract.tick,
                     source_at=later,observed_at=later)
    result = b.poll(None,{p.contract.symbol:exit_q},now=later)
    assert result['entry'] == 'WORKER_UNAVAILABLE'
    assert result['protection'][identity] == 'TARGET'
    assert not ledger.snapshot()['positions']
    ledger.close()


@pytest.mark.parametrize('case,expected', [('late','COMPLETION_EXPIRED'),('rewind','COMPLETION_EXPIRED'),
    ('off','ENTRIES_DISABLED'),('policy','POLICY_CHANGED'),('stale','NO_ELIGIBLE_PREMIUM_PLAN'),
    ('stale_underlying','UNDERLYING_QUOTE_UNAVAILABLE'),('unconfirmed','TRIGGER_NOT_CURRENT'),
    ('denied','ENTRY_NOT_AUTHORIZED')])
def test_completion_respects_current_time_policy_quotes_and_permission(worker,tmp_path,case,expected):
    ledger = OptionsPaper(tmp_path/'o.sqlite3')
    ledger.set_enabled(True)
    b = bridge(ledger,worker,authorize=lambda *a: case != 'denied')
    h,uq,cs,qs = data()
    b.submit(h,uq,cs,qs,now=NOW)
    now = NOW
    if case == 'late': now += timedelta(seconds=3)
    if case == 'rewind': now -= timedelta(seconds=1)
    if case == 'off': ledger.set_enabled(False)
    if case == 'policy': b.controller.settings = replace(SETTINGS,target_distance=D('.05'))
    if case == 'stale': now += timedelta(seconds=2); qs = {s:replace(q,source_at=NOW-timedelta(seconds=1)) for s,q in qs.items()}; uq = replace(uq,source_at=now,observed_at=now)
    if case == 'stale_underlying': now += timedelta(seconds=2); uq = replace(uq,source_at=NOW-timedelta(seconds=1)); qs = {s:replace(q,source_at=now,observed_at=now) for s,q in qs.items()}
    if case == 'unconfirmed': uq = replace(uq,bid=D(100))
    assert drain(b,uq,qs,now)['entry'] == expected
    assert not ledger.snapshot()['events']
    b.close(); ledger.close()


def test_timeout_discards_pending_and_disables_without_retry(worker,tmp_path):
    ledger = OptionsPaper(tmp_path/'o.sqlite3'); ledger.set_enabled(True)
    b = bridge(ledger,worker)
    h,uq,cs,qs = data()
    b.submit(h,uq,cs,qs,now=NOW)
    worker._deadline = time.monotonic()-1
    result = b.poll(uq,qs,now=NOW)
    assert result['entry'] == 'WORKER_UNAVAILABLE'
    assert not ledger.enabled and b.pending is None
    assert not ledger.snapshot()['positions']
    assert b.submit(h,uq,cs,qs,now=NOW)['entry'] == 'WORKER_UNAVAILABLE'
    b.close(); ledger.close()


def test_restart_preserves_consumed_signal(tmp_path):
    h,uq,cs,qs = data()
    path = tmp_path/'o.sqlite3'
    for turn in range(2):
        w = OptionsSignalWorker(timeout_seconds=10); w.start(); wait(w)
        ledger = OptionsPaper(path); ledger.set_enabled(True)
        b = bridge(ledger,w)
        try:
            b.submit(h,uq,cs,qs,now=NOW)
            assert drain(b,uq,qs,NOW)['entry'] == ('OPENED' if turn == 0 else 'DUPLICATE_SIGNAL')
            assert len(ledger.snapshot()['positions']) == 1
        finally:
            b.close(); ledger.close()


def test_wire_snapshot_and_byte_limit(worker):
    h,uq,cs,qs = data()
    raw = _pack(h,uq,cs,qs,NOW,SETTINGS)
    worker.submit('ASSESS',raw)
    raw['contracts'][0]['symbol'] = 'OTHER'
    assert wait(worker)['payload']['proposals'][0]['plan']['contract']['symbol'] == cs[0].symbol
    raw = _pack(h,uq,cs,qs,NOW,SETTINGS)
    bars = []
    for i in range(48):
        b = raw['bars'][0].copy()
        b['end'] = (NOW-timedelta(minutes=47-i)).isoformat()
        for k in ('high','low','close'): b[k] = '00000000000000000000000000000100.00000000'
        bars.append(b)
    raw['bars'] = bars
    with pytest.raises(ValueError): worker.submit('ASSESS',raw)
    assert worker.state == WorkerState.READY
