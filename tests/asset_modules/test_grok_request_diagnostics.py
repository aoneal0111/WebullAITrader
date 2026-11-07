"""Request failures are observable without provider bodies or credentials."""
import io
import json
import ssl
from urllib.error import HTTPError, URLError
import pytest
from app.asset_modules.grok import GrokClient, GrokUnavailable
from tests.asset_modules.test_grok import response


@pytest.mark.parametrize('failure,expected',[
    (TimeoutError('PRIVATE'),'TIMEOUT'),
    (URLError(TimeoutError('PRIVATE')),'TIMEOUT'),
    (URLError('PRIVATE_NETWORK_URL'),'NETWORK_ERROR'),
    (ssl.SSLError('PRIVATE_TLS'),'TLS_ERROR'),
    (URLError(ssl.SSLError('PRIVATE_TLS')),'TLS_ERROR'),
    (OSError('PRIVATE'),'TRANSPORT_OTHER'),
])
def test_transport_categories_never_include_exception_text(failure,expected):
    times=iter([10.,16.42]);calls=[]
    def transport(request):calls.append(request);raise failure
    c=GrokClient('PRIVATE_KEY','grok-test',transport=transport,latency_clock=lambda:next(times))
    with pytest.raises(GrokUnavailable,match='^GROK_REQUEST_FAILED$'):
        c.request('MASTER','',{'mode':'PAPER'},{})
    d=c.diagnostics()
    assert d==dict(attempts_completed=1,responses_received=0,answers_parsed=0,
                   latency_ms_total=6420,latency_ms_max=6420,failures={expected:1})
    assert len(calls)==1 and 'PRIVATE' not in json.dumps(d)
    d['failures'].clear()
    assert c.diagnostics()['failures']=={expected:1}


@pytest.mark.parametrize('code',[400,401,403,408,429,500,503])
def test_http_status_only_is_counted_and_body_closed(code):
    body=io.BytesIO(b'PRIVATE_BODY')
    error=HTTPError('https://PRIVATE_URL',code,'PRIVATE_MESSAGE',{'SECRET':'PRIVATE_HEADER'},body)
    def transport(_):raise error
    c=GrokClient('PRIVATE_KEY','grok-test',transport=transport)
    with pytest.raises(GrokUnavailable):c.request('MASTER','',{'mode':'PAPER'},{})
    assert c.diagnostics()['failures']=={f'HTTP_{code}':1}
    assert body.closed and 'PRIVATE' not in json.dumps(c.diagnostics())


@pytest.mark.parametrize('raw,category',[
    (b'invalid PRIVATE','RESPONSE_VALIDATION'),
    (b'x'*65537,'RESPONSE_TOO_LARGE'),
    (response({},status='incomplete'),'INCOMPLETE_RESPONSE'),
    (response({},error={'message':'PRIVATE'}),'PROVIDER_RESPONSE_ERROR'),
], ids=['invalid-json','oversized-response','incomplete-response','provider-error'])
def test_response_failures_distinguished_from_transport(raw,category):
    c=GrokClient('PRIVATE_KEY','grok-test',transport=lambda _:raw)
    with pytest.raises(GrokUnavailable):c.request('MASTER','',{'mode':'PAPER'},{})
    d=c.diagnostics()
    assert d['responses_received']==1 and d['answers_parsed']==0
    assert d['failures']=={category:1} and 'PRIVATE' not in json.dumps(d)


def test_success_latency_and_cooldown_have_separate_accounting():
    times=iter([0.,6.42]);c=GrokClient('key','grok-test',transport=lambda _:response({}),
        clock=lambda:0,latency_clock=lambda:next(times))
    assert c.request('MASTER','',{'mode':'PAPER'},{})=={}
    with pytest.raises(GrokUnavailable,match='COOLDOWN'):c.request('MASTER','',{'mode':'PAPER'},{})
    assert c.requests==1 and c.diagnostics()['answers_parsed']==1
    assert c.diagnostics()['latency_ms_total']==6420 and c.diagnostics()['failures']=={}


def test_slow_valid_reply_expires_without_execution_authority():
    from datetime import timedelta
    from tests.asset_modules.test_grok_observer import NOW,BASE,snap
    from app.asset_modules.grok_observer import GrokObserverTrial
    now=[NOW]
    def transport(_):
        now[0]+=timedelta(seconds=6.42)
        return response({'view':'WATCH','reason':'Synthetic observation'})
    c=GrokClient('key','grok-test',transport=transport)
    record=GrokObserverTrial(c,BASE,clock=lambda:now[0]).observe(snap())
    assert record.status=='ADVICE_EXPIRED' and not record.execution_authorized
    assert c.diagnostics()['answers_parsed']==1 and c.requests==1
