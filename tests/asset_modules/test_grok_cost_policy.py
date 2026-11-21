from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import json
import pytest

from app.asset_modules.grok import GrokClient,GrokUnavailable
from app.asset_modules.grok_cost_policy import install,load,estimate,policy,FILENAME
from app.asset_modules.grok_engine_teams import TeamBudget
from app.asset_modules.grok_warrior_control import ControlClient
from tests.asset_modules.test_continuous_paper import operation,persistent_control
from tests.asset_modules.test_grok_warrior_control import control,point
from tests.asset_modules.test_controlled_paper_service import setup,NOW
from tests.asset_modules.test_grok_runtime_handoff import warrior
from tests.asset_modules.test_grok import response


def test_budget_survives_restart_rolls_at_utc_midnight_and_reserves_native_capacity(tmp_path):
    now=[NOW];exp=operation(tmp_path,lambda:now[0]);root=exp.path.parent
    install(root,'1.00');b=TeamBudget(root,clock=lambda:now[0])
    amount=dict(model='grok-4.7',reserved_micro_usd=250000)
    b.reserve('WARRIOR_MOMENTUM_V1','DIRECTOR',{'event_id':'d'},cost_estimate=amount)
    with pytest.raises(GrokUnavailable,match='DIRECTOR_COST'):
        b.reserve('WARRIOR_MOMENTUM_V1','DIRECTOR',{'event_id':'d2'},cost_estimate=amount)
    for n in range(3):b.reserve('WARRIOR_MOMENTUM_V1','NATIVE_REVIEW',{'event_id':str(n)},cost_estimate=amount)
    restarted=TeamBudget(root,clock=lambda:now[0])
    with pytest.raises(GrokUnavailable,match='DAILY_COST'):
        restarted.reserve('WARRIOR_MOMENTUM_V1','NATIVE_REVIEW',{'event_id':'over'},cost_estimate=amount)
    report=restarted.report();assert report['reserved_attempts']==4
    assert report['cost_budget']['reserved_estimate_usd']=='1'
    assert report['cost_budget']['state']=='DAILY_BUDGET_REACHED'
    now[0]+=timedelta(days=1)
    restarted.reserve('WARRIOR_MOMENTUM_V1','NATIVE_REVIEW',{'event_id':'new-day'},cost_estimate=amount)
    assert restarted.report()['cost_budget']['requests_reserved_today']==1


def test_parallel_reservations_cannot_overspend_or_leave_extra_call_rows(tmp_path):
    exp=operation(tmp_path);install(exp.path.parent,'0.50')
    b=TeamBudget(exp.path.parent,clock=lambda:NOW)
    def reserve(n):
        try:return b.reserve('WARRIOR_MOMENTUM_V1','NATIVE_REVIEW',{'event_id':str(n)},cost_estimate=dict(model='grok-4.7',reserved_micro_usd=250000))
        except GrokUnavailable:return None
    with ThreadPoolExecutor(max_workers=8) as pool:result=list(pool.map(reserve,range(8)))
    assert sum(x is not None for x in result)==2
    assert b.report()['reserved_attempts']==2 and b.report()['cost_budget']['reserved_estimate_usd']=='0.5'


def test_unknown_pricing_and_missing_estimates_never_dispatch_or_reserve(tmp_path):
    exp=operation(tmp_path);install(exp.path.parent,'1.00');b=TeamBudget(exp.path.parent,clock=lambda:NOW)
    with pytest.raises(GrokUnavailable):estimate('grok-new','',{}, {},2048)
    with pytest.raises(GrokUnavailable):b.reserve('WARRIOR_MOMENTUM_V1','DIRECTOR',{'event_id':'x'})
    assert b.report()['reserved_attempts']==0
    assert estimate('grok-4.7','',{}, {},2048)['reserved_micro_usd']>2048*6
    with pytest.raises(GrokUnavailable):estimate('grok-4.7','x'*128000,{}, {},2048)


def test_policy_install_does_not_change_operation_spec_and_rejects_invalid_file(tmp_path):
    exp=operation(tmp_path);before=exp.path.read_bytes()
    install(exp.path.parent,'1');assert exp.path.read_bytes()==before
    install(exp.path.parent,'2');assert load(exp.path.parent)['daily_micro_usd']==2000000
    assert len(list(exp.path.parent.glob('grok-cost-policy-backup-*.json')))==1
    for value in ('NaN','-1','0','1.001','101'):
        with pytest.raises(ValueError):policy(value)
    (exp.path.parent/FILENAME).write_text('{}')
    with pytest.raises(ValueError):TeamBudget(exp.path.parent)


def enable(control,tmp_path):
    c=persistent_control(control,tmp_path)
    install(c.root,'1');c.budget=TeamBudget(c.root,clock=c.clock)
    c.client=GrokClient('fixture','grok-4.7',transport=lambda _:response(dict(action='PASS',symbol='SPY',reason='fixture')),clock=lambda:100)
    c.client.maximum_requests=None;c.client.role_cooldown_seconds=0
    c._contexts['SPY']=(point(),NOW)
    return c


def test_unchanged_evidence_is_skipped_but_new_bar_and_price_move_reconsidered(control,tmp_path):
    from dataclasses import replace
    c=enable(control,tmp_path)
    try:
        c._review_director();c._completed.clear();before=c.client.requests
        p=point();future=NOW+timedelta(seconds=301)
        c.clock=lambda:future;c._contexts['SPY']=(p,future)
        c._review_director();assert c.client.requests==before
        assert c._input_counts['unchanged_director_context_skipped']==1
        c._contexts['SPY']=(replace(p,observation=replace(p.observation,price=Decimal('6.04'))),future)
        c._review_director();assert c.client.requests==before+1
        c._completed.clear();future+=timedelta(seconds=301);c._contexts['SPY']=(replace(p,bars=(replace(p.bars[0],timestamp=future-timedelta(minutes=1)),)),future)
        c._review_director();assert c.client.requests==before+2
    finally:c.stop();c.producer.request_stop()


def test_pending_native_review_yields_director_without_budget_use(control,tmp_path,monkeypatch):
    c=enable(control,tmp_path)
    monkeypatch.setattr(c.producer,'status',lambda:{'review':{'handoff':{'pending_entries':1,'pending_acknowledgements':0}}})
    try:
        c._review_director();assert c.client.requests==0 and c.budget.report()['reserved_attempts']==0
        assert c._input_counts['native_review_priority_yield']==1
    finally:c.stop();c.producer.request_stop()


def test_cost_refusal_prevents_transport_and_keeps_native_review_available(control,tmp_path):
    c=enable(control,tmp_path);calls=[]
    c.client._transport=lambda request:calls.append(request) or response({})
    try:
        c.budget.reserve('WARRIOR_MOMENTUM_V1','DIRECTOR',{'event_id':'spent'},cost_estimate=dict(model='grok-4.7',reserved_micro_usd=250000))
        with pytest.raises(GrokUnavailable,match='DIRECTOR_COST'):
            ControlClient(c,'DIRECTOR').request('WARRIOR_MOMENTUM_V1','',{'mode':'PAPER','event_id':'blocked'},{})
        assert not calls and c.budget.report()['reserved_attempts']==1
        ControlClient(c,'NATIVE_REVIEW').request('WARRIOR_MOMENTUM_V1','',{'mode':'PAPER','event_id':'native'},{})
        assert len(calls)==1 and c.budget.report()['reserved_attempts']==2
        assert c.budget.report()['unfinalized_attempts']==1 # manually seeded reservation stays charged
    finally:c.stop();c.producer.request_stop()


def test_full_watchlist_still_rotates_under_cost_controls(control,tmp_path):
    from dataclasses import replace
    c=enable(control,tmp_path)
    try:
        c._contexts={}
        for index in range(12):
            symbol=f'S{index:02}';p=point()
            c._contexts[symbol]=(replace(p,observation=replace(p.observation,symbol=symbol)),NOW)
        for batch in range(3):
            now=NOW+timedelta(seconds=60*batch);c.clock=lambda:now
            c._contexts={s:(p,now) for s,(p,_) in c._contexts.items()}
            c._review_director();c._completed.clear()
        assert len(c._coverage)==12 and c.client.requests==3
        c._write_status()
        report=json.loads((c.root/'warrior-control-status.json').read_text())
        assert report['requests']['cost_budget']['daily_budget_usd']=='1'
        assert report['watchlist_coverage']['cost_controls']['minimum_batch_seconds']==60
    finally:c.stop();c.producer.request_stop()


def test_unknown_model_blocks_before_network_and_reservation(control,tmp_path):
    c=enable(control,tmp_path);calls=[];c.client.model='grok-unpriced'
    c.client._transport=lambda request:calls.append(request) or response({})
    try:
        with pytest.raises(GrokUnavailable,match='COST_PRICING'):
            ControlClient(c,'NATIVE_REVIEW').request('MASTER','',{'mode':'PAPER','event_id':'unknown'},{})
        assert not calls and c.budget.report()['reserved_attempts']==0
    finally:c.stop();c.producer.request_stop()
