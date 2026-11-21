from dataclasses import replace
from datetime import timedelta
import json
from threading import Event
import time
import pytest
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok import GrokClient
from app.asset_modules.grok_background_observer import BackgroundAdvisers,run_background
from app.asset_modules.grok_observer_capture import _encode
from tests.asset_modules.test_grok_observer import NOW,BASE,snap
from tests.asset_modules.test_grok_observer_context import evidence
from tests.asset_modules.test_grok import response


def item(engine=EngineId.WARRIOR,at=NOW,event='native'):
    s=replace(snap(engine,event),source='WEBULL_OPENAPI',captured_at=at,bid_at=at,ask_at=at)
    e=replace(evidence(s),source='WEBULL_OPENAPI',captured_at=at)
    shift=at.replace(second=0,microsecond=0)-NOW
    e=replace(e,bars=tuple(replace(b,closed_at=b.closed_at+shift,available_at=b.available_at+shift) for b in e.bars))
    return s,e


def until(predicate):
    deadline=time.monotonic()+3
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('Worker completion timeout')
        time.sleep(.002)


def test_warrior_latency_never_blocks_scalper_or_ingestion_and_latest_replaces_backlog():
    entered=Event();release=Event();calls=[];clock=[NOW];timer=[0.]
    def transport(request):
        role=json.loads(request.data)['input'][1]['content'];calls.append(json.loads(role))
        if calls[-1]['engine']==EngineId.WARRIOR.value:
            entered.set();assert release.wait(3)
        return response(dict(view='WATCH',reason='Background structure note'))
    c=GrokClient('fixture','grok-test',transport=transport,clock=lambda:timer[0])
    p=BackgroundAdvisers(c,BASE,clock=lambda:clock[0],timer=lambda:timer[0])
    assert p.offer(*item())=='PENDING';p.pump();assert entered.wait(2)
    try:
        p.offer(*item(EngineId.SCALPER));p.pump()
        until(lambda:p.snapshot()['in_flight']==1)
        records=p.drain();assert len(records)==1 and records[0].engine==EngineId.SCALPER.value
        for n in range(1,5):
            clock[0]=NOW+timedelta(seconds=n/10)
            assert p.offer(*item(at=clock[0],event=f'e{n}')) in {'PENDING','LATEST_REPLACED'}
        assert p.snapshot()['pending']==1 and c.requests==2
        assert p.snapshot()['scheduling']['LATEST_REPLACED']==3
    finally:release.set();p.finish(2)
    assert p.snapshot()['stopped'] and p.snapshot()['pending']==0
    assert all(not r.execution_authorized for r in p.drain())


def test_stale_pending_context_never_retimed_into_a_request():
    t=[0.];clock=[NOW];calls=[]
    c=GrokClient('fixture','grok-test',transport=lambda r:calls.append(r) or response({}))
    p=BackgroundAdvisers(c,BASE,clock=lambda:clock[0],timer=lambda:t[0])
    p.offer(*item());clock[0]+=timedelta(seconds=3);t[0]=3;p.pump()
    assert not calls and p.snapshot()['scheduling']['STALE_BEFORE_REQUEST']==1
    p.finish(0)


def test_stop_is_nonblocking_with_pending_provider_and_drops_mailbox():
    entered=Event();release=Event()
    def transport(_):entered.set();release.wait(3);return response(dict(view='PASS',reason='Observation'))
    c=GrokClient('fixture','grok-test',transport=transport)
    p=BackgroundAdvisers(c,BASE,clock=lambda:NOW)
    p.offer(*item());p.pump();assert entered.wait(2)
    start=time.monotonic();p.stop();assert time.monotonic()-start<.1
    assert p.offer(*item(EngineId.SCALPER))=='STOPPED'
    state=p.finish(0);assert state['in_flight']==1
    release.set();p.finish(2)
    assert c.requests==1 and p.snapshot()['in_flight']==0


@pytest.mark.parametrize('case',['fixture','wrong_baseline','mismatch'])
def test_invalid_context_cannot_reach_provider(case):
    s,e=item();calls=[]
    if case=='fixture':s=replace(s,source='RECORDED_FIXTURE')
    if case=='wrong_baseline':s=replace(s,baseline_sha256='b'*64)
    if case=='mismatch':e=replace(e,event_id='different')
    p=BackgroundAdvisers(GrokClient('fixture','grok-test',transport=lambda r:calls.append(r)),BASE,clock=lambda:NOW)
    with pytest.raises(ValueError):p.offer(s,e)
    p.pump();assert not calls;p.finish(0)


def test_background_trial_retains_slow_answer_as_expired_private_research(tmp_path):
    source=tmp_path/'capture';s,e=item();source.write_bytes(_encode(s,e,BASE,NOW,'WEBULL_PROVIDER_QUOTE_TIME_SHARED'))
    clock=[NOW];timer=[0.];calls=[]
    def transport(request):
        calls.append(request);clock[0]+=timedelta(seconds=13.692)
        return response(dict(view='WATCH',reason='PRIVATE_RESEARCH_NOTE'))
    c=GrokClient('fixture','grok-test',transport=transport)
    def wait(_):
        until(lambda:c.diagnostics()['attempts_completed']==1)
        timer[0]=301
    r=run_background(source,BASE,tmp_path/'out',c,clock=lambda:clock[0],timer=lambda:timer[0],wait=wait)
    assert r['statuses']=={'ADVICE_EXPIRED':1} and r['advisory_records_retained']==1
    assert r['orders']==0 and not r['execution_authorized'] and not r['desktop_decision_input']
    assert 'PRIVATE_RESEARCH_NOTE' not in json.dumps(r)
    note=json.loads((tmp_path/'out/private-advisory-state.json').read_text())['records'][0]
    assert note['reason']=='PRIVATE_RESEARCH_NOTE' and note['view']=='WATCH'
    assert note['status']=='ADVICE_EXPIRED' and note['evidence_age_ms']==13692
    assert note['advisory_only'] and not note['execution_authorized']
    assert r['accounting_final'] and len(calls)==1


def test_capture_rewrite_cancels_pending_without_new_requests(tmp_path):
    s,e=item();source=tmp_path/'capture';line=_encode(s,e,BASE,NOW,'WEBULL_PROVIDER_QUOTE_TIME_SHARED')
    source.write_bytes(line);timer=[0.]
    c=GrokClient('fixture','grok-test',transport=lambda _:response(dict(view='PASS',reason='Observation')))
    def wait(_):
        source.write_bytes(b'');timer[0]+=.1
    r=run_background(source,BASE,tmp_path/'out',c,clock=lambda:NOW,timer=lambda:timer[0],wait=wait)
    assert r['stop_reason']=='CAPTURE_REVISED' and r['inference_attempts']<=1
    assert r['scheduling']['stopped']


def test_request_reservations_are_globally_bounded_and_cooldown_is_per_owner():
    clock=[NOW];t=[0.];calls=[]
    c=GrokClient('fixture','grok-test',transport=lambda r:calls.append(r) or response(dict(view='PASS',reason='Observation')),
                 clock=lambda:t[0])
    p=BackgroundAdvisers(c,BASE,clock=lambda:clock[0],timer=lambda:t[0])
    for n in range(10):
        t[0]=61*n;clock[0]=NOW+timedelta(seconds=t[0])
        for owner in (EngineId.WARRIOR,EngineId.SCALPER):p.offer(*item(owner,clock[0],f'n{n}'))
        p.pump();until(lambda:p.snapshot()['in_flight']==0);p.drain()
    assert len(calls)==20 and p.snapshot()['submitted']==20
    t[0]+=61;clock[0]+=timedelta(seconds=61)
    assert p.offer(*item(at=clock[0],event='extra'))=='REQUEST_LIMIT'
    p.pump();p.finish(1);assert len(calls)==20


def test_older_context_cannot_replace_latest_after_worker_has_started():
    clock=[NOW+timedelta(seconds=1)];entered=Event();release=Event()
    def transport(_):entered.set();release.wait(3);return response(dict(view='PASS',reason='Observation'))
    c=GrokClient('fixture','grok-test',transport=transport)
    p=BackgroundAdvisers(c,BASE,clock=lambda:clock[0])
    p.offer(*item(at=clock[0],event='new'));p.pump();assert entered.wait(2)
    try:
        assert p.offer(*item())=='OLDER_OR_EQUAL_CONTEXT'
        assert p.snapshot()['pending']==0
    finally:release.set();p.finish(2)


def test_invalid_slow_answer_does_not_become_retained_research():
    from app.asset_modules.grok_observer import GrokObserverTrial
    now=[NOW]
    def transport(_):
        now[0]+=timedelta(seconds=6)
        return response(dict(view='BUY',reason='Untrusted command',quantity=100))
    c=GrokClient('fixture','grok-test',transport=transport)
    record=GrokObserverTrial(c,BASE,clock=lambda:now[0]).observe(item()[0])
    assert record.status=='INVALID_ADVICE' and record.view is None
    assert not record.execution_authorized


def test_background_runner_subprocess_is_network_disconnected(tmp_path):
    import subprocess,sys
    source=tmp_path/'capture';s,e=item()
    source.write_bytes(_encode(s,e,BASE,NOW,'WEBULL_PROVIDER_QUOTE_TIME_SHARED'))
    code="""
import socket,sys
from pathlib import Path
from threading import Event
from tests.asset_modules.test_grok_background_observer import NOW,BASE,until
from tests.asset_modules.test_grok import response
from app.asset_modules.grok import GrokClient
from app.asset_modules.grok_background_observer import run_background
socket.socket=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No network'))
t=[0.];c=GrokClient('fixture','grok-test',transport=lambda _:response(dict(view='WATCH',reason='Fixture')))
def wait(_):
    until(lambda:c.diagnostics()['attempts_completed']==1)
    t[0]=301
r=run_background(Path(sys.argv[1]),BASE,Path(sys.argv[2]),c,clock=lambda:NOW,timer=lambda:t[0],wait=wait)
assert r['statuses']=={'OBSERVED':1} and r['orders']==0 and r['accounting_final']
"""
    r=subprocess.run([sys.executable,'-c',code,str(source),str(tmp_path/'out')],capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stderr
