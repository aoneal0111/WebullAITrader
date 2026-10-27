from datetime import UTC,datetime,timedelta
import json
import pytest
from app.webull.equity_quote_source_probe import describe,run

NOW=datetime(2026,10,27,15,0,tzinfo=UTC)


class Market:
    def __init__(self,fail=False):self.calls=[];self.fail=fail
    def get_snapshot(self,**kw):
        self.calls.append(('SNAPSHOT',kw))
        if self.fail:raise ValueError('PRIVATE_KEY_OR_PROVIDER_BODY')
        at=str(int((NOW-timedelta(seconds=15)).timestamp()*1000))
        return dict(data=[dict(symbol=kw['symbols'][0],quote_time=at,bid='PRIVATE_PRICE',secret='PRIVATE_SECRET')])
    def get_quotes(self,*args,**kw):
        self.calls.append(('DEPTH',args,kw))
        return dict(data=dict(timestamp=NOW.isoformat(),bids=[dict(price='PRIVATE_PRICE',size='5')],
                              asks=[dict(price='PRIVATE_PRICE',size='6')]))


def test_four_bounded_reads_compare_known_fields_without_private_data_or_execution():
    m=Market();waits=[];r=run(m,clock=lambda:NOW,wait=waits.append)
    assert len(m.calls)==4 and r['market_data_attempts']==4 and waits==[1,1,1]
    assert [x[0] for x in m.calls]==['SNAPSHOT','DEPTH','SNAPSHOT','DEPTH']
    assert m.calls[0][1]==dict(symbols=['SPY'],category='US_STOCK',extend_hour_required=True)
    assert m.calls[1][1:]==(('SPY','US_STOCK'),{'depth':1})
    assert r['records'][0]['inspection']['timestamp_fields'][0]['age_ms']==15000
    assert r['records'][1]['inspection']['timestamp_fields'][0]['age_ms']==0
    assert 'PRIVATE' not in json.dumps(r) and not r['execution_authorized']
    assert r['orders']==r['grok_requests']==0 and not r['settings_modified']


def test_failures_do_not_retry_or_expose_exception_body():
    m=Market(True);r=run(m,clock=lambda:NOW,wait=lambda _:None)
    assert len(m.calls)==4 and [x['state'] for x in r['records']]==[
        'READ_FAILED','RESPONSE_RECEIVED','READ_FAILED','RESPONSE_RECEIVED']
    assert 'PRIVATE' not in json.dumps(r)


def test_unknown_fields_and_invalid_times_are_not_echoed():
    r=describe(dict(access_token='PRIVATE',error='PRIVATE',quote_time='PRIVATE',
                    data={'PRIVATE_KEY':'PRIVATE'}),NOW)
    assert 'PRIVATE' not in json.dumps(r)
    assert r['timestamp_fields']==[dict(path='quote_time',parsed=False)]


def test_large_list_and_future_timestamp_are_bounded_and_explicit():
    r=describe(dict(data=[dict(timestamp=(NOW+timedelta(seconds=1)).isoformat())]*10000),NOW)
    assert len(r['timestamp_fields'])==2 and r['timestamp_fields'][0]['age_ms']==-1000
    assert not r['execution_authorized']


def test_cli_requires_opt_in_before_network_or_output(tmp_path,monkeypatch):
    from app.webull.equity_quote_source_probe import main
    monkeypatch.setattr('sys.argv',['probe','--repo',str(tmp_path),'--output',str(tmp_path/'out')])
    monkeypatch.setattr('app.webull.sdk_market_data.create_official_data_client',lambda **k:pytest.fail('No client'))
    with pytest.raises(SystemExit) as e:main()
    assert e.value.code==2 and not (tmp_path/'out').exists()


def test_response_is_closed_even_if_inspection_fails():
    class Response:
        status_code=200
        closed=0
        def json(self):raise ValueError('PRIVATE')
        def close(self):self.closed+=1
    responses=[]
    class BadMarket:
        def get_snapshot(self,**k):r=Response();responses.append(r);return r
        def get_quotes(self,*a,**k):r=Response();responses.append(r);return r
    report=run(BadMarket(),clock=lambda:NOW,wait=lambda _:None)
    assert all(r.closed==1 for r in responses)
    assert all(r['state']=='READ_FAILED' for r in report['records'])


def test_http_error_bodies_are_never_read():
    class Response:
        status_code=403
        def json(self):pytest.fail('Must not read error body')
    class Denied:
        def get_snapshot(self,**k):return Response()
        def get_quotes(self,*a,**k):return Response()
    report=run(Denied(),clock=lambda:NOW,wait=lambda _:None)
    assert all(r['diagnostics']=={'http_status':403} for r in report['records'])
