from dataclasses import replace, FrozenInstanceError
from datetime import timedelta
from decimal import Decimal as D
import json
from unittest.mock import patch

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_observer_context import ContextBar, StrategyContext, offline_check
from tests.asset_modules.test_grok_observer import NOW, BASE, snap, fixture


def evidence(snapshot=None):
    s=snapshot or snap()
    bars=tuple(ContextBar(NOW-timedelta(minutes=4-i),NOW-timedelta(minutes=4-i),
        D(100),D('100.1'),D('99.9'),D(100),D(1000)) for i in range(5))
    return StrategyContext(s.engine,s.event_id,s.symbol,BASE,NOW,s.source,'FORMING','NO_ENTRY',bars)


def test_roles_keep_distinct_context_and_no_executable_fields():
    trial,requests,_=fixture()
    records=[]
    for engine in (EngineId.WARRIOR,EngineId.SCALPER):
        s=snap(engine)
        records.append(trial.observe(s,strategy_context=evidence(s)))
    assert all(r.status=='OBSERVED' and not r.execution_authorized and len(r.context_sha256)==64 for r in records)
    assert records[0].context_sha256!=records[1].context_sha256
    focuses=[]
    for request in requests:
        context=json.loads(request['input'][1]['content'])
        e=context['strategy_evidence'];focuses.append(e['role_focus'])
        assert e['engine']==context['engine'] and len(e['bars'])==5
        assert 'completed-bar strategy evidence' in request['input'][0]['content']
        assert not {'quantity','order_id','account_id','positions','pnl','api_key'}&e.keys()
        assert not request['store'] and 'tools' not in request
    assert focuses[0]!=focuses[1]


@pytest.mark.parametrize('change',[
    dict(engine=EngineId.SCALPER),dict(event_id='other'),dict(symbol='MSFT'),dict(baseline_sha256='b'*64),
    dict(captured_at=NOW+timedelta(seconds=1)),dict(source='ALPACA_SIP'),
    dict(setup_state='BUY NOW'),dict(baseline_result='PLACE_ORDER'),dict(bars=()),
])
def test_context_mismatch_or_arbitrary_instructions_never_call_provider(change):
    trial,requests,_=fixture()
    with pytest.raises(ValueError):trial.observe(snap(),strategy_context=replace(evidence(),**change))
    assert not requests


@pytest.mark.parametrize('case',['gap','duplicate','future_availability','old_history','quote_before_bar','too_many','mutable'])
def test_noncausal_or_unbounded_context_is_rejected(case):
    s=snap();e=evidence(s);bars=e.bars
    if case=='gap':bars=(replace(bars[0],closed_at=bars[0].closed_at-timedelta(minutes=1)),)+bars[1:]
    elif case=='duplicate':bars=(bars[1],)+bars[1:]
    elif case=='future_availability':bars=bars[:-1]+(replace(bars[-1],available_at=NOW+timedelta(seconds=1)),)
    elif case=='old_history':bars=tuple(replace(b,closed_at=b.closed_at-timedelta(minutes=2),available_at=b.available_at-timedelta(minutes=2)) for b in bars)
    elif case=='quote_before_bar':s=replace(s,bid_at=NOW-timedelta(seconds=1))
    elif case=='too_many':bars=bars*7
    else:bars=list(bars)
    trial,requests,_=fixture()
    with pytest.raises(ValueError):trial.observe(s,strategy_context=replace(e,bars=bars))
    assert not requests


@pytest.mark.parametrize('updates',[dict(high=D(99)),dict(low=D(101)),dict(close=D('NaN')),
    dict(volume=D(-1)),dict(open=D('1e-20')),dict(closed_at=NOW+timedelta(seconds=1)),
    dict(available_at=NOW-timedelta(seconds=1))])
def test_invalid_completed_bars_are_refused(updates):
    with pytest.raises(ValueError):replace(evidence().bars[-1],**updates)


def test_evidence_is_immutable_and_slow_advice_stays_expired():
    e=evidence()
    with pytest.raises(FrozenInstanceError):e.baseline_result='ENTRY_CANDIDATE'
    trial,requests,_=fixture(delay=3)
    result=trial.observe(snap(),strategy_context=e)
    assert result.status=='ADVICE_EXPIRED' and not result.execution_authorized
    assert len(requests)==1


def test_order_shaped_advice_still_rejected_with_strategy_context():
    trial,_,_=fixture(reply=dict(view='BUY',reason='Execute now',quantity=10))
    result=trial.observe(snap(),strategy_context=evidence())
    assert result.status=='INVALID_ADVICE' and result.view is None and not result.execution_authorized


def test_offline_context_fixtures_need_neither_network_nor_credentials():
    with (patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network forbidden')),
          patch('os.getenv',side_effect=AssertionError('credentials forbidden'))):
        report=offline_check()
    assert report['network_requests']==report['orders']==0 and report['simulated_provider_requests']==2
    assert not report['desktop_wiring'] and not report['online_credentials_validated']


def test_module_entrypoint_uses_canonical_context_types():
    import subprocess, sys
    result=subprocess.run([sys.executable,'-m','app.asset_modules.grok_observer_context'],
                          capture_output=True,text=True,timeout=10,check=True)
    report=json.loads(result.stdout)
    assert report['network_requests']==report['orders']==0
    assert len(report['records'])==2 and all(r['status']=='OBSERVED' for r in report['records'])
