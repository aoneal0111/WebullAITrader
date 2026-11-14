from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal as D
from hashlib import sha256
import json
from unittest.mock import patch

import pytest

from app.equity_overhaul.history_crosscheck import (parse_daily, compare_daily, daily_from_minutes,
    run_crosscheck, CheckError, fetch_daily, MAX_RESPONSE)
from app.equity_overhaul.validation import SYMBOLS, frozen_plan
from tests.equity_overhaul.test_research_v2 import session


def payload(symbol='SPY', split=False):
    return json.dumps({'chart':{'error':None,'result':[{'meta':{'symbol':symbol,'currency':'USD',
        'exchangeTimezoneName':'America/New_York'},'timestamp':[int(datetime(2026,7,1,13,30,tzinfo=UTC).timestamp())],
        'indicators':{'quote':[dict(open=[100],high=[100.2],low=[99.8],close=[100.1],volume=[390000])],
                      'adjclose':[{'adjclose':[50]}]},
        'events':{'splits':{'x':{'numerator':2}} if split else {},'dividends':{'x':{'amount':.1}}}}]}}).encode()


def test_raw_quotes_not_adjusted_close_date_timezone_and_corporate_actions():
    rows,events=parse_daily(payload(),'SPY')
    assert rows[date(2026,7,1)]['close']==D('100.1')
    assert events==dict(split_events=0,dividend_events=1,null_rows=0)
    local=daily_from_minutes(session('SPY'))
    r=compare_daily(local,rows,events)
    assert r['status']=='DAILY_PRICES_WITHIN_DIAGNOSTIC_TOLERANCE'
    assert r['compared_dates']==1 and r['price_difference_dates']==0
    _,events=parse_daily(payload(split=True),'SPY')
    assert compare_daily(local,rows,events)['status']=='INCONCLUSIVE_SPLIT_ADJUSTMENT'


def test_differences_missing_null_and_invalid_provider_are_not_silent_success():
    local=daily_from_minutes(session('SPY'));other,events=parse_daily(payload(),'SPY')
    other[date(2026,7,1)]['close']=D('99.9')
    r=compare_daily(local,other,events)
    assert r['status']=='PRICE_DIFFERENCES_REQUIRE_REVIEW' and r['difference_examples'][0]['fields']==['close']
    assert compare_daily(local,{},events)['status']=='MISSING_SECOND_SOURCE_DATES'
    with pytest.raises(CheckError,match='IDENTITY'):parse_daily(payload('QQQ'),'SPY')
    with pytest.raises(CheckError,match='BOUND'):parse_daily(b' '*(MAX_RESPONSE+1),'SPY')
    doc=json.loads(payload());doc['chart']['result'][0]['indicators']['quote'][0]['close']=[None]
    rows,events=parse_daily(json.dumps(doc).encode(),'SPY')
    assert rows=={} and events['null_rows']==1
    doc=json.loads(payload());doc['chart']['result'][0]['indicators']['quote'][0]['high']=[99]
    with pytest.raises(CheckError,match='OHLCV'):parse_daily(json.dumps(doc).encode(),'SPY')


def fixture(root):
    inputs=[]
    for s in SYMBOLS:
        raw=json.dumps([asdict(b) for b in session(s)],default=str).encode()
        name=s+'-normalized.json';(root/name).write_bytes(raw)
        inputs.append(dict(file=name,sha256=sha256(raw).hexdigest()))
    manifest=dict(source='SYNTHETIC_TEST',plan_document=frozen_plan(),normalized_inputs=inputs)
    raw=json.dumps(manifest).encode();(root/'manifest.json').write_bytes(raw)
    report=dict(scope='OFFLINE_EQUITY_TEMPORAL_SCREEN',plan_document=manifest['plan_document'],
        data=dict(manifest_sha256=sha256(raw).hexdigest(),normalized_inputs={x['file']:x['sha256'] for x in inputs}))
    path=root/'report.json';path.write_text(json.dumps(report));return path


def test_capture_every_symbol_small_output_read_only_history_and_no_promotion(tmp_path):
    report=fixture(tmp_path);before={p.name:sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    calls=[]
    def fetch(s,start,end):
        calls.append((s,start,end));return payload(s)
    out=run_crosscheck(tmp_path,report,tmp_path/'other',fetch=fetch,now=datetime(2026,10,24,tzinfo=UTC),progress=lambda *a,**k:None)
    assert len(calls)==6 and len(out['results'])==6 and len(out['second_source']['responses'])==6
    assert all(start.hour==4 and end==datetime(2026,10,24,tzinfo=UTC) for _,start,end in calls)
    assert out['status']=='DAILY_AGREEMENT_ONLY' and out['promotion']=='NONE' and not out['execution_authorized']
    assert len(json.dumps(out))<20000
    assert all(sha256((tmp_path/n).read_bytes()).hexdigest()==h for n,h in before.items())
    with (tmp_path/'SPY-normalized.json').open('ab') as f:f.write(b' ')
    with pytest.raises(ValueError,match='hash mismatch'):
        run_crosscheck(tmp_path,report,tmp_path/'bad',fetch=fetch)
    assert not (tmp_path/'bad').exists() and len(calls)==6


def test_failure_is_preserved_for_all_symbols_without_retry_or_false_agreement(tmp_path):
    report=fixture(tmp_path);calls=[]
    def broken(s,*args):calls.append(s);raise CheckError('HTTP_429')
    out=run_crosscheck(tmp_path,report,tmp_path/'other',fetch=broken,progress=lambda *a,**k:None)
    assert len(calls)==6 and out['status']=='REVIEW_REQUIRED'
    assert all(c['error']=='HTTP_429' for c in out['results'])
    assert json.loads((tmp_path/'other'/'manifest.json').read_text())['responses']==[]


def test_network_request_is_fixed_public_bounded_and_does_not_read_credentials():
    from unittest.mock import MagicMock
    response=MagicMock();response.__enter__.return_value.read.return_value=payload()
    opener=MagicMock();opener.open.return_value=response
    with patch('urllib.request.build_opener',return_value=opener):
        fetch_daily('SPY',datetime(2025,1,1,tzinfo=UTC),datetime(2026,1,1,tzinfo=UTC))
    req=opener.open.call_args.args[0]
    assert req.full_url.startswith('https://query1.finance.yahoo.com/v8/finance/chart/SPY?')
    assert set(k.lower() for k in req.headers)=={'user-agent','accept'}
    response.__enter__.return_value.read.assert_called_once_with(MAX_RESPONSE+1)
    with pytest.raises(CheckError,match='SYMBOL'):fetch_daily('UNKNOWN',None,None)
