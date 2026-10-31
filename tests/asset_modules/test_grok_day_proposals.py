from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.asset_modules.grok_day_proposals import review

NOW=datetime(2026,10,29,14,tzinfo=UTC)


def objects():
    snapshot=SimpleNamespace(engine=SimpleNamespace(value='QUICK_SCALPER'),event_id='test',symbol='XYZ',captured_at=NOW,bid=10,ask=10.01,bid_at=NOW,ask_at=NOW)
    context=SimpleNamespace(payload=lambda _:dict(baseline_result='BUY',bars=[],role_focus='test'))
    client=Mock();client.request.return_value=dict(action='BUY',entry='10',stop='9.95',target='10.10',reason='Test only')
    return client,snapshot,context


def test_proposal_has_no_order_or_fill_authority_and_hides_native_result():
    client,snapshot,context=objects()
    result=review(client,snapshot,context,clock=lambda:NOW+timedelta(seconds=10))
    assert result['action']=='BUY' and result['status']=='PROPOSED_ONLY'
    assert result['orders']==result['simulated_fills']==0
    assert not result['execution_authorized']
    assert 'baseline_result' not in client.request.call_args.args[2]['strategy_evidence']
    assert client.request.call_args.args[2]['mode']=='PAPER'


@pytest.mark.parametrize('age',[-1,31])
def test_late_or_future_proposals_never_become_actionable(age):
    client,snapshot,context=objects()
    result=review(client,snapshot,context,clock=lambda:NOW+timedelta(seconds=age))
    assert result['status']=='EXPIRED_PROPOSAL' and not result['execution_authorized']


@pytest.mark.parametrize('change',[dict(stop='11'),dict(target='9'),dict(entry='NaN'),dict(action='PLACE_ORDER'),dict(reason='a'*301),dict(entry={})])
def test_untrusted_provider_geometry_or_schema_is_rejected(change):
    client,snapshot,context=objects();client.request.return_value.update(change)
    result=review(client,snapshot,context,clock=lambda:NOW)
    assert result['status']=='PROVIDER_OR_PROPOSAL_UNAVAILABLE'
    assert result['orders']==0


def test_provider_failure_does_not_dispatch_anything():
    client,snapshot,context=objects();client.request.side_effect=RuntimeError('private provider body')
    result=review(client,snapshot,context,clock=lambda:NOW)
    assert 'private provider body' not in str(result)
    assert result['status']=='PROVIDER_OR_PROPOSAL_UNAVAILABLE'


def test_real_grok_client_accepts_paper_context_and_records_structured_intentions():
    import json
    from app.asset_modules.grok import GrokClient
    from tests.asset_modules.test_grok_observer import snap, NOW as CAPTURE_TIME
    from tests.asset_modules.test_grok_observer_context import evidence
    sent=[]
    def transport(request):
        sent.append(json.loads(request.data))
        answer=dict(action='BUY',entry='100.1',stop='100',target='100.5',reason='Synthetic proposal')
        return json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
            content=[dict(type='output_text',text=json.dumps(answer))])])).encode()
    client=GrokClient('fixture-only','grok-4.7',transport=transport)
    snapshot=snap()
    result=review(client,snapshot,evidence(snapshot),clock=lambda:CAPTURE_TIME+timedelta(seconds=1))
    assert result['status']=='PROPOSED_ONLY' and result['orders']==0
    assert client.requests==1 and len(sent)==1
    assert 'tools' not in sent[0] and sent[0]['store'] is False


def test_day_runner_reads_daily_capture_in_separate_process_contract(tmp_path,monkeypatch):
    import json,time
    from dataclasses import replace
    from hashlib import sha256
    import app.asset_modules.grok_day_proposals as module
    import app.configuration.loader as loader
    from app.asset_modules.grok import GrokClient
    from app.asset_modules.daily_research_journal import canonical
    from app.asset_modules.grok_observer_capture import _encode
    from app.equity_overhaul.paper_experiment import create,load
    from tests.asset_modules.test_grok_observer import snap, NOW as CAPTURE_TIME, BASE
    from tests.asset_modules.test_grok_observer_context import evidence
    repo=tmp_path/'repo';repo.mkdir();trial=tmp_path/'trial';create(trial)
    _,spec=load(trial/'experiment.json');session=tmp_path/'daily';session.mkdir()
    (trial/'recording-session.json').write_text(json.dumps(dict(session=str(session),trial_id=spec['trial_id'])))
    source={'app/x.py':'a'*64}
    (session/'research-manifest.json').write_text(json.dumps(dict(source_sha256=sha256(canonical(source)).hexdigest(),
        configuration_sha256='b'*64,baseline_sha256=BASE,paper_experiment=spec)))
    private=session/'private-capture';private.mkdir()
    snapshot=replace(snap(),source='WEBULL_OPENAPI');context=replace(evidence(snapshot),source='WEBULL_OPENAPI')
    (private/'private-observer-context.jsonl').write_bytes(_encode(snapshot,context,BASE,CAPTURE_TIME,'WEBULL_PROVIDER_QUOTE_TIME_SHARED'))
    monkeypatch.setattr(module,'source_manifest',lambda _:source)
    monkeypatch.setattr(module,'configuration_digest',lambda _:'b'*64)
    monkeypatch.setattr(loader,'load_configuration',lambda _:SimpleNamespace(environment=SimpleNamespace(value='PAPER'),live_trading_enabled=False,grok_paper_entries_enabled=False))
    def transport(request):
        answer=dict(action='PASS',entry='',stop='',target='',reason='Synthetic observation')
        return json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
            content=[dict(type='output_text',text=json.dumps(answer))])])).encode()
    client=GrokClient('fixture-only','grok-4.7',transport=transport)
    monkeypatch.setattr(module,'configured_client',lambda *a,**k:client)
    ticks=[0]
    def pause(seconds):ticks[0]+=seconds;time.sleep(.005)
    module.run(repo,trial,max_seconds=3,clock=lambda:CAPTURE_TIME,timer=lambda:ticks[0],pause=pause)
    result=json.loads((trial/'grok-order-intentions/summary.json').read_text())
    assert result['requests_scheduled']==1 and result['records']==1
    assert result['orders']==result['simulated_fills']==0
    assert result['actions']=={'PASS':1}
