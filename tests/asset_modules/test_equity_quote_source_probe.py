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
        return dict(data=dict(quote_time=NOW.isoformat(),bids=[dict(price='PRIVATE_PRICE',size='5')],
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


def checkpoint(tmp_path,*,captured=NOW,record_at=NOW,age=15,symbol='FAST',status='CHECKPOINT',reason='QUOTE_REJECTED_BID_STALE'):
    value=dict(status=status,captured_at=captured.isoformat(),runtime_started_at=(NOW-timedelta(hours=1)).isoformat(),
        secret='PRIVATE',entry_conversion=dict(funnel_records=[dict(symbol=symbol,
            timestamp=record_at.isoformat(),stage='EXECUTION_QUOTE_REJECTED',outcome=reason,
            bid_timestamp_age=age,price='PRIVATE_PRICE',account='PRIVATE_ACCOUNT')]))
    path=tmp_path/'current.json';path.write_text(json.dumps(value));return path


def test_selects_recent_native_stale_bid_rejections_and_exports_no_private_fields(tmp_path):
    from app.webull.equity_quote_source_probe import select_recent_rejections
    path=checkpoint(tmp_path);value=json.loads(path.read_text())
    records=value['entry_conversion']['funnel_records']
    records.append({**records[0],'symbol':'OTHER','timestamp':(NOW-timedelta(seconds=1)).isoformat()})
    records.append({**records[0],'timestamp':(NOW-timedelta(seconds=2)).isoformat()})
    path.write_text(json.dumps(value))
    result=select_recent_rejections(tmp_path,now=NOW)
    assert result['symbols']==['FAST','OTHER'] and len(result['evidence'])==2
    assert result['status']=='RECENT_STALE_QUOTE_REJECTIONS_SELECTED'
    assert result['evidence'][0]['recorded_bid_age_ms']==15000
    assert 'PRIVATE' not in json.dumps(result) and 'price' not in json.dumps(result)


@pytest.mark.parametrize('changes',[
    {'captured':NOW-timedelta(seconds=121)}, {'captured':NOW+timedelta(seconds=1)},
    {'record_at':NOW-timedelta(seconds=301)}, {'record_at':NOW+timedelta(seconds=1)},
    {'age':-.5}, {'age':5}, {'reason':'QUOTE_REJECTED_LAST_TRADE_STALE'},
    {'status':'FINAL'}, {'symbol':'INVALID/SECRET'},
],ids=['stale-checkpoint','future-checkpoint','old-record','future-record','future-quote',
       'not-stale','last-only','stopped-runtime','invalid-symbol'])
def test_old_unrelated_or_invalid_evidence_never_selects_a_symbol(tmp_path,changes):
    from app.webull.equity_quote_source_probe import select_recent_rejections
    checkpoint(tmp_path,**changes)
    assert select_recent_rejections(tmp_path,now=NOW)['symbols']==[]


def test_two_rounds_have_eight_reads_with_counterbalanced_endpoint_order():
    market=Market();waits=[]
    report=run(market,symbols=('FAST','OTHER'),rounds=2,clock=lambda:NOW,wait=waits.append)
    assert len(market.calls)==report['market_data_attempts']==8 and waits==[1]*7
    assert [r['operation'] for r in report['records']]==[
        'SNAPSHOT','DEPTH','SNAPSHOT','DEPTH','DEPTH','SNAPSHOT','DEPTH','SNAPSHOT']
    assert all(r['quote_time_samples']==2 and not r['quote_time_advanced'] for r in report['source_summary'])
    assert not report['execution_authorized']


@pytest.mark.parametrize('symbols,rounds',[(('A','B','C'),1),(('A','A'),1),(('bad',),1),(('A',),3),(('A',),True)])
def test_invalid_request_bounds_fail_before_any_call(symbols,rounds):
    market=Market()
    with pytest.raises(ValueError):run(market,symbols=symbols,rounds=rounds,wait=lambda _:None)
    assert market.calls==[]


def test_missing_directory_and_byte_bound_are_explicit(tmp_path):
    from app.webull.equity_quote_source_probe import select_recent_rejections
    assert select_recent_rejections(tmp_path/'absent',now=NOW)['status']=='CHECKPOINT_DIRECTORY_MISSING'
    (tmp_path/'large.json').write_bytes(b'x'*(16*1024*1024+1))
    result=select_recent_rejections(tmp_path,now=NOW)
    assert result['status']=='CHECKPOINT_BYTE_LIMIT' and result['symbols']==[]


def test_no_matching_rejections_constructs_no_client_or_market_request(tmp_path,monkeypatch):
    from app.webull.equity_quote_source_probe import main
    from types import SimpleNamespace
    monkeypatch.chdir(tmp_path)
    c=SimpleNamespace(environment=SimpleNamespace(value='PAPER'),live_trading_enabled=False,
        grok_paper_entries_enabled=False,market_data=SimpleNamespace(api_key='PRIVATE',api_secret='PRIVATE',
            api_base_url='https://api.webull.com',environment=SimpleNamespace(value='PRODUCTION')))
    monkeypatch.setattr('app.configuration.loader.load_configuration',lambda _:c)
    monkeypatch.setattr('app.webull.sdk_market_data.create_official_data_client',lambda **k:pytest.fail('No client'))
    monkeypatch.setattr('sys.argv',['probe','--repo',str(tmp_path),'--output',str(tmp_path/'report.json'),
        '--checkpoint-directory',str(tmp_path/'missing'),'--rounds','2','--read'])
    assert main()==0
    report=json.loads((tmp_path/'report.json').read_text())
    assert report['market_data_attempts']==0 and report['selection']['symbols']==[]
    assert 'PRIVATE' not in json.dumps(report)


def test_selection_does_not_fall_back_to_another_runtime_when_latest_has_no_rejection(tmp_path):
    from app.webull.equity_quote_source_probe import select_recent_rejections
    import os
    path=checkpoint(tmp_path);old=tmp_path/'old.json';path.rename(old)
    latest=checkpoint(tmp_path,reason='QUOTE_REJECTED_LAST_TRADE_STALE')
    os.utime(old,(10,10));os.utime(latest,(20,20))
    result=select_recent_rejections(tmp_path,now=NOW)
    assert result['symbols']==[] and result['status']=='NO_RECENT_STALE_QUOTE_REJECTIONS'
