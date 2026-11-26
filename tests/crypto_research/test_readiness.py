from dataclasses import replace
from datetime import timedelta

import pytest

from app.crypto_research.readiness import check
from app.crypto_research import CryptoPair
from app.crypto_research.provider import CryptoRateLimitError
from tests.crypto_research.test_runtime import T0,observation


class Provider:
    def __init__(self,rows=None,error=None):self.calls=0;self.rows=rows;self.error=error
    def discover(self,pairs):return pairs
    def snapshots(self,pairs):
        self.calls+=1
        if self.error:raise self.error
        return (observation(),observation('ETH')) if self.rows is None else self.rows


def test_three_bounded_samples_are_read_only_and_have_no_prices_or_secrets():
    p=Provider();r=check(p,clock=lambda:T0)
    assert p.calls==3 and r['ready'] and len(r['samples'])==3
    assert r['orders']==r['grok_requests']==r['strategy_changes']==0
    assert all(s['complete'] for s in r['samples'])
    assert 'price' not in str(r) and 'secret' not in str(r)


@pytest.mark.parametrize('rows',[(observation(),),
    (observation(at=T0-timedelta(seconds=3)),observation('ETH')),
    (observation(at=T0+timedelta(seconds=1)),observation('ETH')),
    (replace(observation(),bid=None),observation('ETH')),
    (observation(),observation('ETH'),observation()),
    (observation(),observation('ETH'),observation('DOGE'))])
def test_missing_duplicate_old_future_or_incomplete_quotes_cannot_pass(rows):
    assert not check(Provider(rows),clock=lambda:T0)['ready']


@pytest.mark.parametrize('error,status',[(PermissionError('SECRET'),'PERMISSION_DENIED'),
    (CryptoRateLimitError('SECRET'),'RATE_LIMITED'),(RuntimeError('SECRET'),'READ_FAILED')])
def test_failures_stop_without_retries_and_preserve_only_safe_category(error,status):
    p=Provider(error=error);r=check(p,clock=lambda:T0)
    assert p.calls==1 and r['status']==status and not r['ready']
    assert 'SECRET' not in str(r)


def test_cli_requires_explicit_reads_and_does_not_construct_clients(tmp_path,monkeypatch):
    from app.crypto_research.readiness import main
    from app.webull.client_factories import MarketDataClientFactory,TradingClientFactory
    monkeypatch.setattr(MarketDataClientFactory,'create',lambda *a,**k:pytest.fail('client constructed'))
    monkeypatch.setattr(TradingClientFactory,'create',lambda *a,**k:pytest.fail('trading constructed'))
    output=tmp_path/'result.json'
    monkeypatch.setattr('sys.argv',['check','--repo',str(tmp_path),'--output',str(output)])
    with pytest.raises(SystemExit) as e:main()
    assert e.value.code==2 and not output.exists()


def test_cli_uses_only_data_factory_and_stops_for_unsafe_mode(tmp_path,monkeypatch):
    import json,os
    from unittest.mock import patch
    from app.crypto_research import readiness
    from app.webull.client_factories import MarketDataClientFactory,TradingClientFactory
    calls=[]
    monkeypatch.setattr(MarketDataClientFactory,'create',lambda *a,**k:calls.append(k) or object())
    monkeypatch.setattr(TradingClientFactory,'create',lambda *a,**k:pytest.fail('trading constructed'))
    monkeypatch.setattr(readiness,'check',lambda p:dict(status='SYNTHETIC_PASS',ready=True,orders=0,grok_requests=0,
        no_retries=p._retry_attempts==0,one_page=p._maximum_pages==1))
    env=tmp_path/'.env';env.write_text('WEBULL_TRADING_ENVIRONMENT=PAPER\nLIVE_TRADING_ENABLED=false\nATLAS_GROK_PAPER_ENTRIES_ENABLED=false\n')
    before=env.read_bytes();output=tmp_path/'ok.json'
    monkeypatch.setattr('sys.argv',['check','--repo',str(tmp_path),'--output',str(output),'--online-read'])
    with patch.dict(os.environ,{},clear=True):assert readiness.main()==0
    result=json.loads(output.read_text())
    assert calls==[dict(timeout_seconds=5)] and result['no_retries'] and result['one_page']
    assert env.read_bytes()==before
    env.write_text('WEBULL_TRADING_ENVIRONMENT=PAPER\nLIVE_TRADING_ENABLED=true\n')
    output=tmp_path/'unsafe.json';calls.clear()
    monkeypatch.setattr('sys.argv',['check','--repo',str(tmp_path),'--output',str(output),'--online-read'])
    with patch.dict(os.environ,{},clear=True):assert readiness.main()==2
    assert not calls and json.loads(output.read_text())['status']=='LOCAL_CONFIGURATION_OR_CLIENT_FAILED'
