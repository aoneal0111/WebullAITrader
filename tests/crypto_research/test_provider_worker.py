from datetime import timedelta
import json

import pytest

from app.crypto_research.provider_worker import DisabledHandoff, check
from app.asset_modules.crypto_signals import CryptoSignalSettings
from tests.asset_modules.test_crypto_worker import data, NOW
from tests.crypto_research.test_bar_alignment import Clock, Provider


def test_actual_spawn_handoff_with_fake_feed_keeps_ledgers_empty():
    c=Clock()
    report=check(Provider(c),clock=c.now,timer=c.timer,sleeper=c.sleep,progress=lambda *a,**k:None)
    assert report['ready'] and report['status']=='BOUNDED_PROVIDER_WORKER_HANDOFF_PASSED'
    assert report['worker_assessments']==6
    assert not any(report['temporary_ledger_state'].values())
    assert report['orders']==report['broker_orders']==report['grok_requests']==0
    for cycle in report['cycles']:
        for row in cycle['symbols']:
            assert row['handoff']['ready'] and row['handoff']['worker_response_verified']
            assert row['handoff']['controller_result']=='NO_CONFIRMED_SPOT_BREAKOUT'


def test_genuine_signal_is_blocked_before_authorization_or_allocation(tmp_path):
    history,quote=data()
    handoff=DisabledHandoff(tmp_path,lambda:NOW)
    try:
        result=handoff.assess(history,quote,CryptoSignalSettings())
        assert result['ready'] and result['controller_result']=='ENTRIES_DISABLED'
        assert result['authorization_attempts']==result['simulated_fills']==result['active_allocations']==0
    finally:handoff.close()


def test_stale_quote_cannot_pass_even_when_worker_has_no_proposal(tmp_path):
    history,quote=data()
    handoff=DisabledHandoff(tmp_path,lambda:NOW+timedelta(seconds=3))
    try:
        result=handoff.assess(history,quote,CryptoSignalSettings())
        assert not result['ready'] and result['quote_age_at_completion_seconds']==3
        assert not any(handoff.state().values())
    finally:handoff.close()


def test_elapsed_completion_is_measured_with_current_time(tmp_path):
    history,quote=data();calls=[]
    def clock():
        calls.append(1)
        return NOW if len(calls)==1 else NOW+timedelta(seconds=3)
    handoff=DisabledHandoff(tmp_path,clock)
    try:
        result=handoff.assess(history,quote,CryptoSignalSettings())
        assert not result['ready'] and result['completion_seconds']==3
        assert result['controller_result']=='COMPLETION_EXPIRED'
        assert not any(handoff.state().values())
    finally:handoff.close()


@pytest.mark.parametrize('mode',['old_quote','gap','missing_quote','revision'])
def test_bad_provider_inputs_cannot_be_promoted_by_worker(mode):
    c=Clock()
    report=check(Provider(c,mode=mode),clock=c.now,timer=c.timer,sleeper=c.sleep,progress=lambda *a,**k:None)
    assert not report['ready']
    assert not any(report['temporary_ledger_state'].values())


def test_provider_exception_preserves_partial_report_and_closes_worker(monkeypatch):
    c=Clock();provider=Provider(c);closed=[]
    original=DisabledHandoff.close
    def close(self):
        original(self);closed.append(self.worker.state.value)
    monkeypatch.setattr(DisabledHandoff,'close',close)
    def fail(*a,**k):raise PermissionError('PRIVATE_BODY')
    provider.historical_bars=fail
    report=check(provider,clock=c.now,timer=c.timer,sleeper=c.sleep,progress=lambda *a,**k:None)
    assert not report['ready'] and report['status']=='PERMISSION_DENIED'
    assert len(report['cycles'])==1 and report['history_attempts']==1
    assert closed==['CLOSED'] and 'PRIVATE_BODY' not in json.dumps(report)


def test_cli_selects_worker_check_without_order_client(tmp_path,monkeypatch):
    from app.crypto_research import readiness,provider_worker
    from app.configuration.loader import load_configuration
    from app.webull.client_factories import MarketDataClientFactory,TradingClientFactory
    config=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','LIVE_TRADING_ENABLED':'false'})
    monkeypatch.setattr('app.configuration.loader.load_configuration',lambda _:config)
    monkeypatch.setattr(MarketDataClientFactory,'create',lambda *a,**k:object())
    monkeypatch.setattr(TradingClientFactory,'create',lambda *a,**k:pytest.fail('order client constructed'))
    monkeypatch.setattr(provider_worker,'check',lambda p:dict(ready=True,status='FIXTURE_ONLY'))
    output=tmp_path/'report.json'
    monkeypatch.setattr('sys.argv',['check','--repo',str(tmp_path),'--output',str(output),'--online-read','--provider-worker'])
    assert readiness.main()==0
    assert json.loads(output.read_text())['status']=='FIXTURE_ONLY'
