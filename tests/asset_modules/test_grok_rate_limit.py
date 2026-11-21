from urllib.error import HTTPError
from email.message import Message
import pytest
from app.asset_modules.grok import GrokClient,GrokUnavailable
from app.asset_modules.grok_failure import failure_diagnostic
from tests.asset_modules.test_grok import response


@pytest.mark.parametrize('header,delay',[('120',120),('999999',3600),('bad',60)])
def test_rate_limit_honors_bounded_retry_after_across_roles_without_retry_or_budget_reservation(header,delay):
    now=[10.];calls=[];reserved=[]
    def transport(_):
        calls.append(1)
        if len(calls)==1:
            headers=Message();headers['Retry-After']=header
            raise HTTPError('https://private.example/',429,'secret provider body',headers,None)
        return response({})
    c=GrokClient('secret-key','grok-test',clock=lambda:now[0],transport=transport)
    with pytest.raises(GrokUnavailable) as error:c.request('MASTER','',{'mode':'PAPER'},{},before_transport=lambda:reserved.append(1))
    assert failure_diagnostic(error.value)=={'failure_category':'RATE_LIMITED','http_status':429,'retry_after_seconds':delay}
    assert 'secret' not in str(error.value)
    with pytest.raises(GrokUnavailable,match='COOLDOWN'):
        c.request('NEWS','',{'mode':'PAPER'},{},before_transport=lambda:reserved.append(2))
    assert calls==[1] and reserved==[1] and c.requests==1
    now[0]+=delay+1
    assert c.request('NEWS','',{'mode':'PAPER'},{})=={}
    assert c.requests==2
