from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

from app.asset_modules.grok import GrokClient,GrokProposalProvider,GrokUnavailable
from app.asset_modules.grok_compact_context import ContextPacker,FORMAT
from app.asset_modules.grok_coordinator import GrokSuite,EngineId
from app.asset_modules.grok_execution_trial import BudgetClient
from app.asset_modules.grok_paper_entries import GrokPaperEntries
from tests.asset_modules.test_grok_paper_entries import prepared,suite,run
from tests.asset_modules.test_controlled_paper_service import setup,NOW
from tests.asset_modules.test_grok_execution_trial import pilot


def bars():
    return [dict(symbol='SPY',timestamp=(NOW-timedelta(minutes=32-i)).isoformat(),
        open='6.01',high=str(6.02+i/1000),low='6.00',close='6.02',volume=str(100+i))
        for i in range(32)]


def unpack(value):
    if isinstance(value,list):return value
    assert value['format']==FORMAT
    return [dict(value['common'],**dict(zip(value['columns'],row,strict=True))) for row in value['rows']]


def test_lossless_candle_cache_never_reuses_quotes_account_or_current_request():
    packer=ContextPacker();original=bars()
    context=dict(mode='PAPER',completed_native_bars=original,quote_at=NOW.isoformat(),account={'position':0},request_id='first')
    first=packer.pack(context)
    assert unpack(first['completed_native_bars'])==original
    assert len(json.dumps(first))<len(json.dumps(context))*.75
    first['completed_native_bars']['common']['symbol']='MUTATED_RETURN_COPY'
    second=packer.pack(dict(context,quote_at=(NOW+timedelta(seconds=1)).isoformat(),account={'position':5},request_id='second'))
    assert packer.hits==1 and unpack(second['completed_native_bars'])==original
    assert second['quote_at']!=context['quote_at'] and second['account']=={'position':5} and second['request_id']=='second'
    changed=[dict(row) for row in original];changed[-1]['close']='6.50'
    third=packer.pack(dict(context,completed_native_bars=changed))
    assert packer.misses==2 and unpack(third['completed_native_bars'])==changed
    assert context['completed_native_bars']==original


def test_crypto_packing_preserves_pair_provenance_and_numeric_types():
    packer=ContextPacker();rows=bars()
    rows[0]['typed']=True
    for i,row in enumerate(rows[1:]):row['typed']=1 if i%2 else 1.0
    context={'completed_provider_bars':{'BTC/USD':rows}}
    restored=unpack(packer.pack(context)['completed_provider_bars']['BTC/USD'])
    assert json.dumps(restored,sort_keys=True)==json.dumps(rows,sort_keys=True)


def test_small_sparse_or_unrelated_evidence_is_not_rewritten():
    packer=ContextPacker()
    context=dict(completed_native_bars=[{'close':'1'},{'close':'2','volume':'3'}],
        private_notes={'rows':bars()},completed_provider_bars={'BTC/USD':[]})
    assert packer.pack(context)==context


def single_pass(transform=None):
    ai,ticks=suite(transform)
    ai=GrokSuite(ai.client,clock=lambda:NOW,single_pass=True)
    ai.client.maximum_output_tokens=512
    return ai,ticks


def test_one_combined_call_submits_two_owners_through_same_durable_controller(setup):
    commands,_,service,_,_,clock=setup
    seen=[]
    ai,_=single_pass(lambda context,answer:seen.append(context))
    entries=[prepared(commands),prepared(commands,EngineId.SCALPER,'QQQ')]
    report=run(GrokPaperEntries(ai,service,authorize=lambda e:True,clock=lambda:clock[0]),entries)
    assert [o.state for o in report.outcomes]==['SUBMITTED','SUBMITTED']
    assert len(commands.order_book.history())==2 and ai.client.requests==1
    assert seen[0]['role']=='MASTER' and seen[0]['evidence']['review_mode']=='COMBINED_NATIVE_ENGINE_AND_MASTER_V1'
    assert {c['engine'] for c in seen[0]['candidates']}=={'WARRIOR_MOMENTUM_V1','QUICK_SCALPER'}
    assert service.status()['reserved_capital']==120


@pytest.mark.parametrize('revocation',['setup','quote','risk','expiry','stop'])
def test_combined_reply_never_bypasses_post_model_execution_checks(setup,revocation):
    commands,_,service,account,quote,clock=setup;valid=[True];enabled=[True]
    def revoke(context,answer):
        if revocation=='setup':valid[0]=False
        elif revocation=='quote':quote[0]=replace(quote[0],ask=quote[0].ask+1)
        elif revocation=='risk':account[0]=replace(account[0],risk_engine_approved=False)
        elif revocation=='expiry':clock[0]=NOW+timedelta(seconds=61)
        else:enabled[0]=False
    ai,_=single_pass(revoke)
    report=GrokPaperEntries(ai,service,authorize=lambda e:valid[0],clock=lambda:clock[0]).review_and_submit(
        [prepared(commands)],engine_evidence={},account_evidence={'mode':'PAPER'},entry_enabled=lambda:enabled[0])
    assert ai.client.requests==1 and not commands.order_book.history()
    assert not any(o.state=='SUBMITTED' for o in report.outcomes)


@pytest.mark.parametrize('change',['unknown','long_reason','wrong_request','extra'])
def test_invalid_compact_model_output_never_creates_order(setup,change):
    commands,_,service,_,_,clock=setup
    def corrupt(context,answer):
        if change=='unknown':answer['selected_ids']=['unknown']
        elif change=='long_reason':answer['reason']='x'*241
        elif change=='wrong_request':answer['request_id']='previous-request'
        else:answer['quantity']='1000'
    ai,_=single_pass(corrupt)
    report=run(GrokPaperEntries(ai,service,authorize=lambda e:True,clock=lambda:clock[0]),[prepared(commands)])
    assert not commands.order_book.history() and not report.outcomes


def test_combined_dedup_keeps_new_engine_evidence_and_original_candidate(setup):
    commands=setup[0];seen=[]
    ai,_=single_pass(lambda context,answer:seen.append(context))
    entry=prepared(commands)
    candidate=replace(entry.candidate,evidence_json='{"setup":"confirmed","typed":1}')
    ai.review_batch([candidate],engine_evidence={entry.candidate.engine.value:{'setup':'confirmed','news':'additional fact','typed':True}},
        account_evidence={'position':0})
    details=seen[0]['evidence']['native_engine_evidence'][entry.candidate.engine.value]
    assert details['additional_evidence']=={'news':'additional fact','typed':True}
    assert details['facts_already_in_candidates']==['setup']
    assert seen[0]['candidates'][0]['evidence']==json.loads(candidate.evidence_json)


def test_compact_sent_context_is_exactly_recorded_with_bounded_output_and_latency(pilot):
    _,lease,_=pilot;requests=[]
    def transport(request):
        requests.append(json.loads(request.data))
        return json.dumps({'status':'completed','output':[{'type':'message','role':'assistant','status':'completed',
            'content':[{'type':'output_text','text':'{"ok":true}'}]}]}).encode()
    client=GrokClient('fixture','grok-test',transport=transport);client.maximum_output_tokens=512
    BudgetClient(client,lease).request('MASTER','fixture',{'mode':'PAPER','completed_native_bars':bars()}, {})
    with sqlite3.connect(lease.db_path) as db:context,result=db.execute('SELECT context,result FROM requests').fetchone()
    result=json.loads(result)
    assert json.loads(context)==json.loads(requests[0]['input'][1]['content'])
    assert requests[0]['max_output_tokens']==512
    assert result['encoding']['sent_context_bytes']<result['encoding']['original_context_bytes']
    assert result['latency_ms']>=0 and lease.request_count()==1


def test_compact_crypto_reasons_are_short_without_changing_default_schema():
    captured=[]
    class Client:
        def request(self,role,prompt,context,schema):
            captured.append(schema)
            return {'proposals':[{'symbol':'BTC/USD','action':'HOLD','reason':'x'*241}]}
    context={'asset':'CRYPTO','mode':'PAPER'}
    assert GrokProposalProvider(Client()).propose(context)[0]['action']=='HOLD'
    with pytest.raises(GrokUnavailable):GrokProposalProvider(Client(),compact=True).propose(context)
    assert captured[0]['properties']['proposals']['items']['properties']['reason']['maxLength']==1000
    assert captured[1]['properties']['proposals']['items']['properties']['reason']['maxLength']==240


def test_latency_report_is_numeric_read_only_and_keeps_unknown_timing_unknown(pilot):
    from app.asset_modules.grok_latency_report import summarize
    root,lease,_=pilot
    one=lease.reserve_request('MASTER',{'mode':'PAPER'})
    two=lease.reserve_request('CRYPTO',{'mode':'PAPER'})
    lease.reserve_request('LEARNING',{'mode':'PAPER'})
    lease.finish_request(one,dict(status='ANSWER_RECEIVED',answer={'reason':'private trading explanation'},latency_ms=100,
        encoding={'original_context_bytes':1000,'sent_context_bytes':500}))
    lease.finish_request(two,dict(status='FAILED_OR_REVOKED',latency_ms=300))
    before=lease.db_path.read_bytes();report=summarize(root)
    assert lease.db_path.read_bytes()==before
    assert report['requests_in_this_ledger']==3 and report['requests_without_latency']==1
    assert report['latency_ms']=={'min':100,'median':200,'p95':300,'max':300}
    assert report['original_context_bytes']==1000 and report['sent_context_bytes']==500
    assert report['statuses']['UNFINALIZED']==1
    assert 'private trading explanation' not in json.dumps(report)
    assert report['network_requests']==report['orders']==0


def test_latency_report_does_not_create_missing_ledger(tmp_path):
    from app.asset_modules.grok_latency_report import summarize
    with pytest.raises(ValueError,match='NOT_PRESENT'):summarize(tmp_path)
    assert not (tmp_path/'grok-request-budget.sqlite3').exists()
