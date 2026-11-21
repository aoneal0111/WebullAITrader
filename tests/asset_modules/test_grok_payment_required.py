from threading import Event, Thread
from urllib.error import HTTPError

import pytest

from app.asset_modules.grok import GrokClient, GrokUnavailable, PAYMENT_RECHECK_SECONDS
from app.asset_modules.grok_failure import failure_diagnostic
from tests.asset_modules.test_grok import response


def test_payment_refusal_blocks_all_roles_before_reservation_and_recovers_with_new_context():
    now=[10.];calls=[];reserved=[]
    def transport(request):
        calls.append(request.data)
        if len(calls)==1:
            raise HTTPError('https://secret.invalid/',402,'private-body-and-key',{},None)
        return response({'decision':'PASS'})
    client=GrokClient('private-key','grok-test',clock=lambda:now[0],transport=transport)
    with pytest.raises(GrokUnavailable) as first:
        client.request('MASTER','',{'mode':'PAPER','event_id':'old'},{},before_transport=lambda:reserved.append(1))
    expected=dict(failure_category='PAYMENT_REQUIRED',http_status=402,retry_after_seconds=300)
    assert failure_diagnostic(first.value)==expected
    for elapsed in (1,10,60,299):
        now[0]=10+elapsed
        for role in ('MASTER','WARRIOR_MOMENTUM_V1','NEWS'):
            with pytest.raises(GrokUnavailable) as blocked:
                client.request(role,'',{'mode':'PAPER','event_id':'different'},{},before_transport=lambda:reserved.append(2))
            assert failure_diagnostic(blocked.value)['failure_category']=='PAYMENT_REQUIRED'
    assert len(calls)==1 and reserved==[1] and client.requests==1
    assert client.diagnostics()['attempts_completed']==1
    assert client.diagnostics()['failures']=={'HTTP_402':1}
    assert client.diagnostics()['provider_access']['required_action']=='CHECK_XAI_API_TEAM_BILLING'
    assert 'private' not in str(first.value)+str(client.diagnostics())
    now[0]=10+PAYMENT_RECHECK_SECONDS
    assert client.request('MASTER','',{'mode':'PAPER','event_id':'fresh'},{},before_transport=lambda:reserved.append(3))=={'decision':'PASS'}
    assert reserved==[1,3] and b'fresh' in calls[-1] and b'old' not in calls[-1]
    assert client.diagnostics()['provider_access']['state']=='NO_PAYMENT_BLOCK_OBSERVED'


@pytest.mark.parametrize('second_failure',[402,429,'timeout','invalid'])
def test_failed_recovery_keeps_backoff_instead_of_reopening_tight_loop(second_failure):
    now=[10.];calls=[]
    def transport(_):
        calls.append(1)
        if len(calls)==1 or second_failure==402:
            raise HTTPError('https://secret.invalid/',402,'secret',{},None)
        if second_failure==429:raise HTTPError('https://secret.invalid/',429,'secret',{},None)
        if second_failure=='timeout':raise TimeoutError('secret')
        return b'invalid'
    client=GrokClient('key','grok-test',clock=lambda:now[0],transport=transport)
    for at in (10,310):
        now[0]=at
        with pytest.raises(GrokUnavailable):client.request('MASTER','',{'mode':'PAPER'},{})
    now[0]=320
    with pytest.raises(GrokUnavailable) as error:client.request('NEWS','',{'mode':'PAPER'},{})
    assert failure_diagnostic(error.value)['failure_category']=='PAYMENT_REQUIRED'
    assert len(calls)==2 and client.diagnostics()['provider_access']['retry_after_seconds']==290


def test_only_one_recovery_probe_is_allowed_in_flight():
    now=[10.];entered=Event();release=Event();calls=[];errors=[]
    def transport(_):
        calls.append(1)
        if len(calls)==1:raise HTTPError('https://secret.invalid/',402,'secret',{},None)
        entered.set();assert release.wait(3)
        return response({})
    client=GrokClient('key','grok-test',clock=lambda:now[0],transport=transport)
    with pytest.raises(GrokUnavailable):client.request('MASTER','',{'mode':'PAPER'},{})
    now[0]=310
    def probe():
        try:client.request('MASTER','',{'mode':'PAPER'},{})
        except Exception as exc:errors.append(exc)
    thread=Thread(target=probe);thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(GrokUnavailable):client.request('NEWS','',{'mode':'PAPER'},{})
        assert len(calls)==2 and client.diagnostics()['provider_access']['recovery_probe_in_progress']
    finally:release.set();thread.join(3)
    assert not thread.is_alive() and not errors
    assert not client.diagnostics()['provider_access']['recovery_probe_in_progress']
