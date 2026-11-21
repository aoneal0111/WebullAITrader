from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json

import pytest

from app.crypto_research.bar_alignment import check
from app.crypto_research.models import CryptoObservation
from tests.crypto_research.test_runtime import T0


class Clock:
    def __init__(self):self.seconds=0.;self.jump=0.
    def now(self):return T0+timedelta(seconds=self.seconds+self.jump)
    def timer(self):return self.seconds
    def sleep(self,n):assert 0<=n<=65;self.seconds+=n


class Provider:
    def __init__(self,c,*,mode='normal'):self.c=c;self.mode=mode;self.events=[];self.initial=None
    def discover(self,pairs):self.events.append('discover');return pairs
    def historical_bars(self,pair,**kw):
        assert kw==dict(timespan='M1',count=64,real_time_required=False)
        self.events.append('bars:'+pair.canonical_symbol);now=self.c.now();self.c.seconds+=.2
        end=now.replace(second=0,microsecond=0)
        if self.initial is None:self.initial=end
        if self.mode=='frozen':end=self.initial
        rows=[dict(timestamp=(end-timedelta(minutes=63-i)).isoformat(),
            open='100',high='100.2',low='99.8',close='100.1') for i in range(64)]
        if self.mode=='gap':del rows[45]
        if self.mode=='duplicate':rows[45]=dict(rows[44])
        if self.mode=='malformed':rows[0]['high']='bad'
        if self.mode=='multiple_incomplete':rows[0]['is_complete']=False
        if self.mode=='oversized':rows.append(dict(rows[-1]))
        if self.mode=='off_grid':
            for i,row in enumerate(rows):row['timestamp']=(end-timedelta(minutes=64-i,seconds=1)).isoformat()
        if self.mode=='revision' and self.c.seconds>60:rows[20]['high']='100.3'
        return tuple(rows)
    def snapshots(self,pairs):
        self.events.append('quotes');now=self.c.now();self.c.seconds+=.2
        result=tuple(CryptoObservation(p,now,D('100.1'),D(100),D('100.1'),None) for p in pairs)
        if self.mode=='old_quote':result=(replace(result[0],timestamp=now-timedelta(seconds=3)),result[1])
        if self.mode=='future_quote':result=(replace(result[0],timestamp=now+timedelta(seconds=1)),result[1])
        if self.mode=='missing_quote':result=result[:1]
        if self.mode=='quote_before_bar':result=(replace(result[0],timestamp=now-timedelta(seconds=1)),result[1])
        if self.mode=='clock_step' and self.c.seconds>60:self.c.jump=1
        return result


def run(mode='normal'):
    c=Clock();p=Provider(c,mode=mode);progress=[]
    r=check(p,clock=c.now,timer=c.timer,sleeper=c.sleep,progress=lambda *a,**k:progress.append(a))
    return r,p,progress


def test_real_adapter_and_signal_function_use_only_completed_causal_data():
    r,p,progress=run()
    assert r['ready'] and r['status']=='BOUNDED_BAR_QUOTE_ALIGNMENT_PASSED'
    assert r['history_attempts']==6 and r['snapshot_attempts']==3
    assert p.events==['discover']+['bars:BTC/USD','bars:ETH/USD','quotes']*3
    assert len(progress)==3 and 130<=r['elapsed_seconds']<140
    assert r['orders']==r['grok_requests']==r['strategy_changes']==r['proposals_submitted']==0
    assert r['assessment_counts']=={'NO_CONFIRMED_SPOT_BREAKOUT':6}
    for cycle in r['cycles']:
        assert all(h['incomplete_rows']==1 and h['completed_bars']==63 for h in cycle['history'])
        assert all(s['worker_bar_count']==48 and s['adapter_status']=='READY' for s in cycle['symbols'])
        assert all(h['received_at']<=cycle['decision_cutoff'] for h in cycle['history'])
    assert all(s['latest_bar_advanced'] for cycle in r['cycles'][1:] for s in cycle['symbols'])
    assert len(json.dumps(r))<20000 and '100.1' not in json.dumps(r)


@pytest.mark.parametrize('mode',['gap','duplicate','malformed','multiple_incomplete','off_grid','old_quote','future_quote','missing_quote','quote_before_bar','frozen','revision','clock_step'])
def test_bad_evidence_never_produces_false_alignment_pass(mode):
    r,_,_=run(mode)
    assert not r['ready']
    assert r['status']=='BAR_OR_QUOTE_ALIGNMENT_FAILED'
    assert r['proposals_submitted']==r['orders']==0


def test_revision_is_compared_on_overlapping_bar_identity():
    r,_,_=run('revision')
    assert any(h['revisions']>0 for cycle in r['cycles'] for h in cycle['history'])


def test_provider_failure_is_sanitized_and_partial_cycle_is_preserved():
    c=Clock();p=Provider(c)
    def fail(*a,**k):raise PermissionError('SECRET_BODY')
    p.historical_bars=fail
    r=check(p,clock=c.now,timer=c.timer,sleeper=c.sleep)
    assert r['status']=='PERMISSION_DENIED' and not r['ready']
    assert r['history_attempts']==1 and r['snapshot_attempts']==0 and len(r['cycles'])==1
    assert 'SECRET_BODY' not in json.dumps(r)


def test_oversized_history_response_stops_before_normalization_and_quotes():
    r,p,_=run('oversized')
    assert r['status']=='READ_FAILED' and not r['ready']
    assert r['history_attempts']==1 and r['snapshot_attempts']==0 and 'quotes' not in p.events


def test_hypothetical_signal_never_enters_a_dispatch_path(monkeypatch):
    calls=[]
    def hypothetical(*a):calls.append(1);return {'action':'BUY','notional':'200'},'PROPOSED'
    monkeypatch.setattr('app.crypto_research.bar_alignment.propose',hypothetical)
    r,_,_=run()
    assert len(calls)==6 and r['assessment_counts']=={'PROPOSED':6}
    assert r['orders']==r['proposals_submitted']==0 and 'notional' not in json.dumps(r)


def test_cli_routes_bar_check_without_running_other_modes(tmp_path,monkeypatch):
    from app.crypto_research import readiness,bar_alignment
    from app.configuration.loader import load_configuration
    from app.webull.client_factories import MarketDataClientFactory,TradingClientFactory
    config=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','LIVE_TRADING_ENABLED':'false'})
    monkeypatch.setattr('app.configuration.loader.load_configuration',lambda _:config)
    monkeypatch.setattr(MarketDataClientFactory,'create',lambda *a,**k:object())
    monkeypatch.setattr(TradingClientFactory,'create',lambda *a,**k:pytest.fail('order client constructed'))
    monkeypatch.setattr(readiness,'check',lambda *a:pytest.fail('short quote mode selected'))
    monkeypatch.setattr(bar_alignment,'check',lambda p:dict(ready=True,status='SYNTHETIC_BAR_CHECK_ONLY'))
    output=tmp_path/'result.json'
    monkeypatch.setattr('sys.argv',['check','--repo',str(tmp_path),'--output',str(output),'--online-read','--bar-alignment'])
    assert readiness.main()==0
    assert json.loads(output.read_text())['status']=='SYNTHETIC_BAR_CHECK_ONLY'
