from dataclasses import replace
from datetime import timedelta
import json

import pytest

from app.crypto_research.reliability import capture
from app.crypto_research.provider import CryptoRateLimitError
from tests.crypto_research.test_runtime import T0,observation


class Clock:
    def __init__(self):self.seconds=0.;self.jump=0.;self.sleeps=[]
    def now(self):return T0+timedelta(seconds=self.seconds+self.jump)
    def timer(self):return self.seconds
    def sleep(self,n):assert 0<=n<=5;self.sleeps.append(n);self.seconds+=n


class Provider:
    def __init__(self,clock,*,error=None,bad=None,latency=.2,jump=0):
        self.clock=clock;self.error=error;self.bad=bad;self.latency=latency;self.jump=jump;self.calls=[]
    def discover(self,pairs):return pairs
    def snapshots(self,pairs):
        self.calls.append(self.clock.seconds);at=self.clock.now();self.clock.seconds+=self.latency
        if len(self.calls)==2:
            self.clock.jump+=self.jump
            if self.error:raise self.error
        rows=(observation(at=at),observation('ETH',at=at))
        return self.bad(rows) if self.bad and len(self.calls)==2 else rows


def run(**kwargs):
    c=Clock();p=Provider(c,**kwargs);progress=[]
    r=capture(p,clock=c.now,timer=c.timer,sleeper=c.sleep,progress=lambda *a,**k:progress.append(a))
    return r,p,c,progress


def test_five_minute_window_without_real_sleep_or_network_is_bounded():
    r,p,c,progress=run()
    assert r['ready'] and r['status']=='BOUNDED_WINDOW_FRESH'
    assert len(p.calls)==r['snapshot_attempts']==r['sampled_slots']==60
    assert 295<=r['elapsed_seconds']<300 and len(progress)==6
    assert all(b-a>=5 for a,b in zip(p.calls,p.calls[1:]))
    assert r['orders']==r['grok_requests']==r['strategy_changes']==0
    assert r['recovery_verification']=='NO_RECOVERY_OBSERVED'
    assert all(x['entry_fresh_samples']==60 for x in r['symbols'].values())
    assert len(json.dumps(r))<100000


def test_actual_transient_failure_then_recovery_is_recorded_without_erasing_failure():
    r,p,_,_=run(error=RuntimeError('PRIVATE_PROVIDER_BODY'))
    assert not r['ready'] and r['status']=='WINDOW_NOT_FULLY_FRESH'
    assert r['recoveries_observed']==1 and r['status_counts']['READ_FAILED']==1
    assert r['fresh_complete_samples']==59 and len(p.calls)==60
    assert all(x['unobserved_slots']==1 and x['quotes_present_samples']==59 for x in r['symbols'].values())
    assert 'PRIVATE_PROVIDER_BODY' not in json.dumps(r)


def test_rate_limit_defers_real_calls_for_at_least_sixty_seconds():
    r,p,_,_=run(error=CryptoRateLimitError('PRIVATE'))
    assert p.calls[2]-p.calls[1]>=60
    assert r['status_counts']['RATE_LIMIT_BACKOFF']>=11
    assert r['recoveries_observed']==1 and not r['ready'] and len(p.calls)<60


def test_permission_failure_stops_early_without_retrying_credentials():
    r,p,_,_=run(error=PermissionError('PRIVATE'))
    assert len(p.calls)==2 and r['status']=='PERMISSION_DENIED' and not r['ready']
    assert r['recoveries_observed']==0


@pytest.mark.parametrize('bad',[
    lambda rows:rows[:1],
    lambda rows:(rows[0],rows[0]),
    lambda rows:(replace(rows[0],timestamp=rows[0].timestamp+timedelta(seconds=1)),rows[1]),
    lambda rows:(replace(rows[0],timestamp=rows[0].timestamp-timedelta(seconds=3)),rows[1]),
    lambda rows:(replace(rows[0],ask=None),rows[1]),
])
def test_single_bad_sample_prevents_a_false_clean_window(bad):
    r,_,_,_=run(bad=bad)
    assert not r['ready'] and r['fresh_complete_samples']==59
    assert r['recoveries_observed']==1


def test_wall_clock_step_and_deadline_fail_closed():
    r,_,_,_=run(jump=1)
    assert r['clock_discontinuities']==1 and not r['ready']
    r,p,_,_=run(latency=6)
    assert r['status']=='DEADLINE_REACHED' and len(p.calls)==50 and not r['ready']


def test_missing_pair_counts_differ_from_a_whole_failed_request():
    r,_,_,_=run(bad=lambda rows:rows[:1])
    eth=r['symbols']['ETH/USD'];btc=r['symbols']['BTC/USD']
    assert eth['quotes_present_samples']==59 and eth['evaluated_symbol_samples']==60
    assert eth['missing_or_duplicate_samples']==1 and eth['unobserved_slots']==0
    assert btc['quotes_present_samples']==60


def test_interrupt_preserves_samples_and_discovery_failure_uses_safe_status():
    c=Clock();p=Provider(c)
    def interrupted(_):raise KeyboardInterrupt
    p.snapshots=interrupted
    r=capture(p,clock=c.now,timer=c.timer,sleeper=c.sleep)
    assert r['status']=='INTERRUPTED' and not r['ready'] and r['snapshot_attempts']==1
    def denied(_):raise PermissionError('SECRET')
    p.discover=denied
    r=capture(p,clock=c.now,timer=c.timer,sleeper=c.sleep)
    assert r['status']=='PERMISSION_DENIED' and r['snapshot_attempts']==0 and 'SECRET' not in str(r)


def test_cli_routes_explicit_reliability_mode_without_real_provider_calls(tmp_path,monkeypatch):
    from app.crypto_research import readiness,reliability
    from app.configuration.loader import load_configuration
    from app.webull.client_factories import MarketDataClientFactory,TradingClientFactory
    config=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','LIVE_TRADING_ENABLED':'false'})
    monkeypatch.setattr('app.configuration.loader.load_configuration',lambda _:config)
    monkeypatch.setattr(MarketDataClientFactory,'create',lambda *a,**k:object())
    monkeypatch.setattr(TradingClientFactory,'create',lambda *a,**k:pytest.fail('order client constructed'))
    monkeypatch.setattr(readiness,'check',lambda *a,**k:pytest.fail('short probe selected'))
    calls=[]
    def capture_stub(provider):
        calls.append(provider._retry_attempts)
        return dict(ready=True,status='SYNTHETIC_RELIABILITY_ONLY')
    monkeypatch.setattr(reliability,'capture',capture_stub)
    output=tmp_path/'result.json'
    monkeypatch.setattr('sys.argv',['check','--repo',str(tmp_path),'--output',str(output),'--online-read','--reliability'])
    assert readiness.main()==0 and calls==[0]
    assert json.loads(output.read_text())['status']=='SYNTHETIC_RELIABILITY_ONLY'
