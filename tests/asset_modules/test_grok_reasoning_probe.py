from datetime import timedelta
import json
import sqlite3

import pytest

from app.asset_modules.grok import GrokClient,configured_client
from app.asset_modules.grok_reasoning_probe import run
from tests.asset_modules.test_grok import response
from tests.asset_modules.test_grok_execution_trial import pilot,NOW,ENV,PARENT_ENV,configuration


@pytest.mark.parametrize('model',['grok-4.5','grok-4.6','grok-4.7'])
def test_verified_models_send_responses_reasoning_low_without_other_changes(model):
    calls=[]
    client=GrokClient('private-key',model,reasoning_effort='low',transport=lambda request:calls.append(request) or response({}))
    client.request('MASTER','fixture',{'mode':'PAPER'},{})
    payload=json.loads(calls[0].data)
    assert payload['reasoning']=={'effort':'low'} and payload['max_output_tokens']==2048
    assert payload['store'] is False and 'tools' not in payload


def test_default_and_unverified_models_do_not_gain_unsupported_parameters():
    calls=[]
    for model in ['grok-4.7','grok-test']:
        GrokClient('key',model,transport=lambda r:calls.append(r) or response({})).request('MASTER','',{'mode':'PAPER'},{})
    assert all('reasoning' not in json.loads(request.data) for request in calls)
    for model,effort in [('grok-test','low'),('grok-4.5','xhigh'),('grok-4.7','none')]:
        with pytest.raises(ValueError):GrokClient('key',model,reasoning_effort=effort)


@pytest.mark.parametrize('model,expected',[('grok-4.7','low'),('grok-test',None)])
def test_only_explicit_pilot_suite_selects_documented_low_effort(pilot,monkeypatch,model,expected):
    from app.composition.grok_paper import create_grok_paper_suite
    from app.composition.runtime_mode import RuntimeMode
    root,lease,_=pilot
    monkeypatch.setenv(ENV,str(lease.path));monkeypatch.setenv(PARENT_ENV,str(root/'experiment.json'))
    monkeypatch.setenv('XAI_API_KEY','offline-private');monkeypatch.setenv('ATLAS_GROK_MODEL',model)
    suite=create_grok_paper_suite(configuration(QUICK_SCALPER_ENABLED='true'),RuntimeMode.PAPER,clock=lambda:NOW)
    assert suite.client.client.reasoning_effort==expected and suite.client.requests==0


def saved(lease):
    context={'mode':'PAPER','role':'MASTER','request_id':'original-request','observed_at':NOW.isoformat(),
        'private_marker':'private-evidence','candidates':[{'candidate_id':'fixture-one','engine':'WARRIOR_MOMENTUM_V1',
            'valid_until':(NOW+timedelta(seconds=60)).isoformat(),'evidence':{'fixture':True}}]}
    lease.reserve_request('MASTER',context)
    return context


@pytest.mark.parametrize('outcome',['valid','timeout','bad_request','unknown','extra'])
def test_probe_uses_one_shared_attempt_and_never_rewrites_saved_times_or_exports_answers(pilot,capsys,outcome):
    root,lease,_=pilot;original=saved(lease);calls=[]
    before=(root/'experiment.json').read_bytes();entries=(root/'entry-attempts.sqlite3').read_bytes()
    def transport(request):
        calls.append(request)
        if outcome=='timeout':raise TimeoutError('private network detail')
        context=json.loads(json.loads(request.data)['input'][1]['content'])
        assert context['observed_at']==original['observed_at'] and context['candidates']==original['candidates']
        assert context['source_request_id']=='original-request' and context['execution_authorized'] is False
        answer={'request_id':context['request_id'],'selected_ids':['fixture-one'],'reason':'private answer text'}
        if outcome=='bad_request':answer['request_id']='original-request'
        elif outcome=='unknown':answer['selected_ids']=['unknown']
        elif outcome=='extra':answer['shares']='1000'
        return response(answer)
    client=GrokClient('private-key','grok-4.7',reasoning_effort='low',transport=transport)
    report=run(root,root,client=client,clock=lambda:NOW)
    assert len(calls)==1 and report['budget_attempts_reserved']==1 and report['paid_attempts_used']==2
    expected='VALID_HISTORICAL_RESPONSE' if outcome=='valid' else 'UNAVAILABLE' if outcome=='timeout' else 'INVALID_STRUCTURED_RESPONSE'
    assert report['status']==expected and report['orders']==0 and report['settings_modified'] is False
    assert (root/'experiment.json').read_bytes()==before and (root/'entry-attempts.sqlite3').read_bytes()==entries
    with sqlite3.connect(lease.db_path) as db:
        assert json.loads(db.execute("SELECT context FROM requests WHERE role='MASTER'").fetchone()[0])==original
        result=json.loads(db.execute("SELECT result FROM requests WHERE role='LEARNING'").fetchone()[0])
        assert result['reasoning_effort']=='low' and result['model']=='grok-4.7'
    printed=capsys.readouterr().out
    assert 'private-evidence' not in printed and 'private answer text' not in printed and 'private-key' not in printed


def test_expired_or_missing_evidence_probe_refuses_before_paid_call(pilot):
    root,lease,_=pilot
    client=GrokClient('key','grok-4.7',reasoning_effort='low',transport=lambda _:pytest.fail('Network called'))
    with pytest.raises(ValueError,match='SAVED_MASTER'):run(root,root,client=client,clock=lambda:NOW)
    saved(lease)
    (root/'PAUSE_GROK_EXECUTION').touch()
    with pytest.raises(ValueError,match='NOT_ACTIVE'):run(root,root,client=client,clock=lambda:NOW+timedelta(minutes=31))
    assert client.requests==0 and lease.request_count()==1


def test_explicit_historical_probe_after_entry_expiry_never_reactivates_execution(pilot):
    from app.asset_modules.grok_execution_trial import ExecutionLease
    from app.asset_modules.grok import GrokUnavailable
    root,lease,_=pilot;saved(lease);at=NOW+timedelta(minutes=31)
    later=ExecutionLease(lease.path,clock=lambda:at)
    assert not later.active() and later.research_active()
    with pytest.raises(GrokUnavailable):later.reserve_request('MASTER',{'mode':'PAPER'})
    with pytest.raises(GrokUnavailable):later.reserve_request('MASTER',{'mode':'PAPER'},research_only=True)
    with pytest.raises(GrokUnavailable):later.reserve_request('LEARNING',{'mode':'PAPER'},research_only=True)
    def transport(request):
        context=json.loads(json.loads(request.data)['input'][1]['content'])
        return response(dict(request_id=context['request_id'],selected_ids=[],reason='Historical fixture'))
    client=GrokClient('key','grok-4.7',reasoning_effort='low',transport=transport)
    report=run(root,root,client=client,clock=lambda:at)
    assert report['status']=='VALID_HISTORICAL_RESPONSE' and report['entry_lease_active'] is False
    assert not later.active() and later.request_count()==2
