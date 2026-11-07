from datetime import UTC,datetime,timedelta
import json
from threading import Event,Thread
import pytest
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_coordinator import Candidate,GrokSuite,IntentKind
from app.asset_modules.grok_decision_council import GrokDecisionCouncil,role_readiness
from app.asset_modules.grok_learning import LearningJournal

NOW=datetime(2026,10,26,15,tzinfo=UTC)


class Client:
    def __init__(self):self.calls=[];self.callback=lambda role:None
    def request(self,role,prompt,context,schema):
        self.calls.append((role,context))
        self.callback(role)
        if role=='LEARNING':return dict(candidate_ids=[context['candidates'][0]['id']],reason='Nominate only')
        return dict(request_id=context['request_id'],selected_ids=[c['candidate_id'] for c in context['candidates'][:2]],reason='Native candidates only')


def candidate(engine,kind=IntentKind.ENTRY):
    management=kind is not IntentKind.ENTRY
    return Candidate(engine,engine.value+'-'+kind.value,NOW+timedelta(seconds=60),'{}',kind,
        lifecycle_id=engine.value+'|position|1' if management else None,
        order_id='native-order' if kind in (IntentKind.CANCEL_ENTRY,IntentKind.REPLACE_ENTRY,IntentKind.TIGHTEN_STOP) else None,
        expected_revision=3 if management else None)


def test_all_engines_all_intents_master_and_learning_are_distinct_and_no_orders(tmp_path):
    client=Client();suite=GrokSuite(client,clock=lambda:NOW)
    journal=LearningJournal(tmp_path/'learning.sqlite3')
    council=GrokDecisionCouncil(suite,journal=journal,clock=lambda:NOW)
    batch=tuple(candidate(e,k) for e in EngineId for k in IntentKind)
    result=council.review('cycle1',batch,engine_evidence={},account_evidence={'scope':'PAPER'},review_learning=True)
    assert result.state=='DECIDED' and not result.execution_authorized and result.promotion=='NONE'
    assert [r for r,c in client.calls]==[e.value for e in EngineId]+['MASTER']
    assert len(result.roles)==7 and dict(result.roles)['LEARNING']=='IDLE'
    assert all(any(c is original for original in batch) for c in result.selected)
    assert all({c['intent'] for c in ctx['candidates']}=={k.value for k in IntentKind}
        for role,ctx in client.calls if role!='MASTER')
    assert not hasattr(council,'place_order') and not hasattr(council,'service')
    calls=len(client.calls)
    assert council.review('cycle1',batch,engine_evidence={},account_evidence={}).state=='DUPLICATE_CYCLE'
    assert len(client.calls)==calls


def test_empty_new_cycle_clears_absent_engine_advice_without_requests():
    client=Client();suite=GrokSuite(client,clock=lambda:NOW)
    council=GrokDecisionCouncil(suite,clock=lambda:NOW)
    council.review('one',[candidate(EngineId.CRYPTO)],engine_evidence={},account_evidence={})
    calls=len(client.calls)
    result=council.review('two',[],engine_evidence={},account_evidence={})
    assert result.state=='HOLD' and not result.selected and len(client.calls)==calls
    assert all(not advice.selected for advice in suite._latest.values())


@pytest.mark.parametrize('case',['duplicate','expired','long','oversize','nan','unknown_engine'])
def test_invalid_batch_rejected_before_any_provider_request(case):
    client=Client();council=GrokDecisionCouncil(GrokSuite(client,clock=lambda:NOW),clock=lambda:NOW)
    c=candidate(EngineId.WARRIOR);batch=[c];evidence={};account={}
    from dataclasses import replace
    if case=='duplicate':batch=[c,c]
    if case=='expired':batch=[replace(c,valid_until=NOW)]
    if case=='long':batch=[replace(c,valid_until=NOW+timedelta(seconds=61))]
    if case=='oversize':account={'large':'x'*65537}
    if case=='nan':account={'exposure':float('nan')}
    if case=='unknown_engine':evidence={'FAKE':{}}
    with pytest.raises(ValueError):council.review('x',batch,engine_evidence=evidence,account_evidence=account)
    assert not client.calls


def test_failure_or_stop_during_engine_review_prevents_master_and_selection():
    for mode in ('fail','stop'):
        client=Client();council=GrokDecisionCouncil(GrokSuite(client,clock=lambda:NOW),clock=lambda:NOW)
        def action(role):
            if mode=='fail':raise RuntimeError('private SDK body')
            council.stop()
        client.callback=action
        r=council.review('x',[candidate(EngineId.WARRIOR)],engine_evidence={},account_evidence={})
        assert not r.selected and not r.execution_authorized and len(client.calls)==1
        assert 'private' not in str(r)


def test_busy_submission_is_not_queued_and_stop_does_not_block():
    client=Client();council=GrokDecisionCouncil(GrokSuite(client,clock=lambda:NOW),clock=lambda:NOW)
    entered=Event();release=Event();results=[]
    def blocked(role):entered.set();assert release.wait(3)
    client.callback=blocked
    thread=Thread(target=lambda:results.append(council.review('one',[candidate(EngineId.WARRIOR)],engine_evidence={},account_evidence={})))
    thread.start()
    try:
        assert entered.wait(2)
        assert council.review('two',[],engine_evidence={},account_evidence={}).state=='BUSY'
        council.stop()
    finally:release.set();thread.join(3)
    assert results[0].state=='STOPPED' and not results[0].selected


def test_readiness_does_not_claim_missing_desktop_adapters():
    r=role_readiness()
    assert len(r['roles'])==7 and not r['desktop_management_dispatch']
    assert not r['options_futures_desktop_feed'] and not r['execution_authorized']



def test_learning_nomination_never_becomes_engine_command_or_policy_change(tmp_path):
    from tests.asset_modules.test_grok_learning import experiment
    journal=LearningJournal(tmp_path/'learning.sqlite3');journal.ingest(experiment())
    c=Client();council=GrokDecisionCouncil(GrokSuite(c,clock=lambda:NOW),journal=journal,clock=lambda:NOW)
    r=council.review('one',[],engine_evidence={},account_evidence={},review_learning=True)
    assert [role for role,ctx in c.calls]==['LEARNING']
    assert dict(r.roles)['LEARNING']=='PAPER_TRIAL_NOMINATED'
    assert not r.selected and not r.execution_authorized and r.promotion=='NONE'
    council.review('two',[],engine_evidence={},account_evidence={},review_learning=True)
    assert len(c.calls)==1  # Durable journal prevents repeating a paid review.


def test_caller_mutation_cannot_change_master_account_context():
    c=Client();account={'cash':'100'}
    c.callback=lambda _:account.update(cash='999999')
    council=GrokDecisionCouncil(GrokSuite(c,clock=lambda:NOW),clock=lambda:NOW)
    council.review('one',[candidate(EngineId.WARRIOR)],engine_evidence={},account_evidence=account)
    assert c.calls[-1][1]['evidence']['account']['cash']=='100'
