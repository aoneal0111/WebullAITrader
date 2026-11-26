from dataclasses import FrozenInstanceError,replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
import json
from unittest.mock import patch

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok import GrokClient
from app.asset_modules.grok_observer import Snapshot,GrokObserverTrial,offline_check

NOW=datetime(2026,10,24,14,tzinfo=UTC)
BASE='a'*64


def snap(engine=EngineId.WARRIOR,event_id='event1'):
    return Snapshot(engine,event_id,'AAPL',D(100),D('100.1'),NOW,NOW,NOW,BASE,'RECORDED_FIXTURE')


def fixture(*,reply=None,delay=0,error=False):
    requests=[];current=[NOW]
    def transport(request):
        requests.append(json.loads(request.data));current[0]+=timedelta(seconds=delay)
        if error:raise RuntimeError('SECRET_PROVIDER_BODY')
        answer=reply if reply is not None else dict(view='WATCH',reason='Observed only.')
        return json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
            content=[dict(type='output_text',text=json.dumps(answer))])])).encode()
    client=GrokClient('test-only','grok-4.7',transport=transport,clock=lambda:current[0].timestamp())
    trial=GrokObserverTrial(client,BASE,clock=lambda:current[0])
    return trial,requests,current


def test_two_owners_record_advice_without_executable_output_or_tools():
    trial,requests,_=fixture()
    for engine in (EngineId.WARRIOR,EngineId.SCALPER):
        r=trial.observe(snap(engine));assert r.status=='OBSERVED' and not r.execution_authorized
        assert r.engine==engine.value
        with pytest.raises(FrozenInstanceError):r.view='BUY'
    assert len(requests)==2
    for p in requests:
        assert 'tools' not in p and p['store'] is False
        context=json.loads(p['input'][1]['content'])
        assert context['purpose']=='OBSERVER_ONLY' and context['execution_authorized'] is False
        assert not {'order_id','account_id','quantity','stop','target','key','positions','pnl'}&context.keys()
    assert not hasattr(trial,'place_order') and not hasattr(trial,'service')


@pytest.mark.parametrize('offset',[-3,1])
def test_stale_and_future_quotes_do_not_call_provider(offset):
    trial,requests,_=fixture()
    r=trial.observe(replace(snap(),bid_at=NOW+timedelta(seconds=offset)))
    assert r.status=='STALE_OR_FUTURE_QUOTE' and requests==[]


@pytest.mark.parametrize('engine',[EngineId.CRYPTO,EngineId.OPTIONS,EngineId.FUTURES,'WARRIOR_MOMENTUM_V1'])
def test_other_engines_or_untyped_engine_are_refused(engine):
    with pytest.raises(ValueError):replace(snap(),engine=engine)


@pytest.mark.parametrize('updates',[dict(bid=D('NaN')),dict(ask=D(99)),dict(bid_at=NOW.replace(tzinfo=None)),
    dict(source='UNKNOWN'),dict(symbol='AAPL;BUY')])
def test_invalid_or_unproven_quotes_are_refused(updates):
    with pytest.raises(ValueError):replace(snap(),**updates)


def test_baseline_mismatch_duplicate_cooldown_and_expired_response():
    trial,requests,_=fixture()
    assert trial.observe(replace(snap(),baseline_sha256='b'*64)).status=='BASELINE_MISMATCH'
    assert not requests
    assert trial.observe(snap()).status=='OBSERVED'
    assert trial.observe(snap()).status=='DUPLICATE'
    assert trial.observe(snap(event_id='event2')).status=='ROLE_COOLDOWN'
    slow,_,_=fixture(delay=3)
    assert slow.observe(snap()).status=='ADVICE_EXPIRED'


@pytest.mark.parametrize('reply',[dict(view='BUY',reason='buy now'),dict(view='WATCH',reason='ok',quantity=100),
    dict(view='WATCH',reason='x'*301)])
def test_executable_or_oversized_model_outputs_are_rejected(reply):
    trial,_,_=fixture(reply=reply)
    r=trial.observe(snap());assert r.status=='INVALID_ADVICE' and r.view is None


def test_bounded_trial_and_sanitized_failures_without_fallback():
    trial,requests,clock=fixture()
    for i in range(20):
        at=NOW+timedelta(seconds=61*i);clock[0]=at
        s=replace(snap(event_id=f'e{i}'),bid_at=at,ask_at=at,captured_at=at)
        assert trial.observe(s).status=='OBSERVED'
    clock[0]+=timedelta(seconds=61)
    s=replace(snap(event_id='extra'),bid_at=clock[0],ask_at=clock[0],captured_at=clock[0])
    assert trial.observe(s).status=='TRIAL_REQUEST_LIMIT' and len(requests)==20
    bad,_,_=fixture(error=True);r=bad.observe(snap())
    assert r.status=='PROVIDER_UNAVAILABLE' and not r.execution_authorized
    assert 'SECRET' not in str(r)


def test_offline_check_never_opens_network_and_does_not_load_local_credentials():
    with (patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network forbidden')),
          patch('os.getenv',side_effect=AssertionError('credentials forbidden'))):
        r=offline_check()
    assert r['network_requests']==r['orders']==0 and r['simulated_provider_requests']==2
    assert not r['desktop_wiring'] and not r['baseline_modified']
