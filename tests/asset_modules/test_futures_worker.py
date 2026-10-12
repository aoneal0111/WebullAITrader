from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import time

import pytest

from app.asset_modules.engine_worker import EngineWorker, WorkerState
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.futures_paper import FuturesPaper
from app.asset_modules.futures_signals import FuturesPaperController
from app.asset_modules.futures_worker import (
    FuturesSignalWorker, FuturesWorkerController, _assess, _pack,
)
from tests.asset_modules.test_futures_signals import NOW, SETTINGS, data


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
    w = FuturesSignalWorker(timeout_seconds=10)
    w.start()
    assert wait(w)['kind'] == 'READY'
    try:
        yield w
    finally:
        w.close()


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_real_subprocess_returns_exact_proposal_and_preserves_observation_protocol(worker, side):
    c,h,q = data(side)
    raw = _pack(c,h,q,NOW,SETTINGS)
    assert worker.assess(c,h,q,NOW,SETTINGS)
    with pytest.raises(RuntimeError): worker.assess(c,h,q,NOW,SETTINGS)
    reply = wait(worker)
    assert reply['payload'] == _assess(raw)
    assert reply['payload']['proposal']['plan']['side'] == side
    assert worker.status()['execution_enabled'] is False
    worker.submit('PING')
    assert wait(worker)['payload']['pid'] == worker.pid


@pytest.mark.parametrize('case', ['extra','symbol','decimal','nonfinite','number','timestamp','bars','bool_hold','future_bar','future_settings'])
def test_transport_rejects_malformed_requests_before_sending(worker, case):
    c,h,q = data()
    raw = _pack(c,h,q,NOW,SETTINGS)
    if case == 'extra': raw['path'] = 'ledger.sqlite3'
    if case == 'symbol': raw['contract']['symbol'] = 'bad symbol'
    if case == 'decimal': raw['contract']['margin'] = 'oops'
    if case == 'nonfinite': raw['quote']['bid'] = 'NaN'
    if case == 'number': raw['contract']['tick'] = .25
    if case == 'timestamp': raw['at'] = '2026-10-12T15:00:00'
    if case == 'bars': raw['bars'] *= 3
    if case == 'bool_hold': raw['settings']['hold_microseconds'] = True
    if case == 'future_bar': raw['bars'][-1]['end'] = (NOW+timedelta(seconds=1)).isoformat()
    if case == 'future_settings': raw['settings']['python_code'] = 'anything'
    with pytest.raises(ValueError): worker.submit('ASSESS',raw)
    assert worker.state == WorkerState.READY
    worker.submit('PING')
    assert wait(worker)['kind'] == 'PONG'


def test_generic_observation_worker_cannot_request_futures_assessment():
    w = EngineWorker(EngineId.FUTURES,policy_version='OBSERVATION_TRANSPORT_V1')
    c,h,q = data()
    with pytest.raises(ValueError): w._request('ASSESS',_pack(c,h,q,NOW,SETTINGS))
    w.close()


def test_reply_verification_rejects_changed_plan_and_boolean_quantity():
    w = FuturesSignalWorker()
    c,h,q = data()
    w._expected = 'ASSESSED'
    w._expected_payload = _pack(c,h,q,NOW,SETTINGS)
    response = _assess(w._expected_payload)
    assert w._valid_payload(response)
    response['proposal']['plan']['stop'] = '1'
    assert not w._valid_payload(response)
    # JSON type must agree even where Python considers True equal to 1.
    w._expected_payload['settings']['max_contracts'] = 1
    response = _assess(w._expected_payload)
    response['proposal']['plan']['quantity'] = True
    assert not w._valid_payload(response)
    w.close()


def bridge(ledger, worker, authorize=lambda *args:True):
    return FuturesWorkerController(worker,FuturesPaperController(ledger,settings=SETTINGS,authorize=authorize))


def drain(b,quotes,now):
    until = time.monotonic()+10
    while time.monotonic() < until:
        result = b.poll(quotes,now=now)
        if result['entry'] != 'PENDING_WORKER': return result
        time.sleep(.01)
    raise AssertionError('Bridge did not complete')


def test_parent_revalidates_current_price_then_protects_after_worker_closes(worker,tmp_path):
    ledger = FuturesPaper(tmp_path/'f.sqlite3')
    b = bridge(ledger,worker)
    c,h,q = data()
    ledger.set_enabled(True)
    assert b.submit(c,h,{c.symbol:q},now=NOW)['entry'] == 'PENDING_WORKER'
    assert b.submit(c,h,{c.symbol:q},now=NOW)['entry'] == 'WORKER_NOT_READY'
    later = NOW+timedelta(seconds=1)
    current = replace(q,bid=D('100.50'),ask=D('100.75'),source_at=later,observed_at=later)
    result = drain(b,{c.symbol:current},later)
    # Current ask, not the child's old ask, drives the parent ledger plan.
    assert result['entry'] == 'ENTRY_EXTENDED'
    assert ledger.snapshot()['positions'] == []
    q = replace(q,source_at=later,observed_at=later)
    b.submit(c,h,{c.symbol:q},now=later)
    result = drain(b,{c.symbol:q},later)
    assert result['entry'] == 'OPENED'
    p = result['proposal'].plan
    identity = result['proposal'].intent_id
    b.close()
    assert not ledger.enabled
    later += timedelta(seconds=1)
    exit_q = replace(q,bid=p.target,ask=p.target+c.tick,source_at=later,observed_at=later)
    result = b.poll({c.symbol:exit_q},now=later)
    assert result['entry'] == 'WORKER_UNAVAILABLE'
    assert result['protection'][identity] == 'TARGET'
    assert not ledger.snapshot()['positions']
    ledger.close()


@pytest.mark.parametrize('case,expected', [('late','COMPLETION_EXPIRED'),('rewind','COMPLETION_EXPIRED'),('off','ENTRIES_DISABLED'),('policy','POLICY_CHANGED'),('stale','QUOTE_UNAVAILABLE'),('denied','ENTRY_NOT_AUTHORIZED')])
def test_completion_cannot_bypass_time_policy_activation_quote_or_permission(worker,tmp_path,case,expected):
    ledger = FuturesPaper(tmp_path/'f.sqlite3')
    ledger.set_enabled(True)
    b = bridge(ledger,worker,authorize=lambda *args:case != 'denied')
    c,h,q = data()
    b.submit(c,h,{c.symbol:q},now=NOW)
    now = NOW
    if case == 'late': now += timedelta(seconds=3); q = replace(q,source_at=now,observed_at=now)
    if case == 'rewind': now -= timedelta(seconds=1); q = replace(q,source_at=now,observed_at=now)
    if case == 'off': ledger.set_enabled(False)
    if case == 'policy': b.controller.settings = replace(SETTINGS,target_r=D(4))
    if case == 'stale': now += timedelta(seconds=2); q = replace(q,source_at=NOW-timedelta(seconds=1))
    assert drain(b,{c.symbol:q},now)['entry'] == expected
    assert not ledger.snapshot()['events']
    b.close(); ledger.close()


def test_failed_worker_disables_new_entries_and_never_retries(worker,tmp_path):
    ledger = FuturesPaper(tmp_path/'f.sqlite3')
    ledger.set_enabled(True)
    b = bridge(ledger,worker)
    c,h,q = data()
    b.submit(c,h,{c.symbol:q},now=NOW)
    worker._process.terminate(); worker._process.join(timeout=2)
    # A buffered valid reply is still possible; force the documented deadline.
    worker._deadline = time.monotonic()-1
    result = b.poll({c.symbol:q},now=NOW)
    assert result['entry'] == 'WORKER_UNAVAILABLE'
    assert not ledger.enabled and b.pending is None
    assert not ledger.snapshot()['positions']
    assert b.submit(c,h,{c.symbol:q},now=NOW)['entry'] == 'WORKER_UNAVAILABLE'
    ledger.close()


def test_restart_worker_and_ledger_cannot_rebuy_same_successful_signal(tmp_path):
    c,h,q = data()
    path = tmp_path/'f.sqlite3'
    for turn in range(2):
        w = FuturesSignalWorker(timeout_seconds=10)
        w.start(); wait(w)
        ledger = FuturesPaper(path); ledger.set_enabled(True)
        b = bridge(ledger,w)
        try:
            b.submit(c,h,{c.symbol:q},now=NOW)
            result = drain(b,{c.symbol:q},NOW)
            assert result['entry'] == ('OPENED' if turn == 0 else 'DUPLICATE_SIGNAL')
            assert len(ledger.snapshot()['positions']) == 1
        finally:
            b.close(); ledger.close()


def test_payload_snapshot_and_byte_budget(worker):
    c,h,q = data()
    payload = _pack(c,h,q,NOW,SETTINGS)
    worker.submit('ASSESS',payload)
    payload['contract']['symbol'] = 'OTHER'
    assert wait(worker)['payload']['proposal']['plan']['contract']['symbol'] == c.symbol
    # Structurally valid but very wide decimal strings still respect 8 KiB framing.
    payload = _pack(c,h,q,NOW,SETTINGS)
    bars = []
    for i in range(48):
        original = payload['bars'][0].copy()
        original['end'] = (NOW-timedelta(minutes=47-i)).isoformat()
        for key in ('high','low','close'): original[key] = '00000000000000000000000000000100.00000000'
        bars.append(original)
    payload['bars'] = bars
    with pytest.raises(ValueError): worker.submit('ASSESS',payload)
    assert worker.state == WorkerState.READY
