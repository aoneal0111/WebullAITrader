from dataclasses import replace
from decimal import Decimal as D
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock
import json
import pytest

from app.asset_modules.grok_warrior_control import WarriorControl,ControlClient
from app.asset_modules.grok import GrokUnavailable
from app.equity_overhaul.paper_experiment import create,load,PaperExperiment
from app.strategies.warrior_momentum.quick_scalper import SymbolOwnershipRegistry,StrategyOwner
from app.strategies.warrior_momentum.models import MinuteBar
from app.strategies.warrior_momentum.forward_models import PointInTimeObservation
from app.momentum_scanner.models import ScannerObservation,CatalystType
from tests.asset_modules.test_controlled_paper_service import setup,fill,NOW
from tests.asset_modules.test_grok_runtime_handoff import warrior
from tests.asset_modules.test_grok_desktop_producer import desktop


def point():
    obs=ScannerObservation('SPY',NOW,D(6),D('5.8'),D(1000),D(10000),None,D('5.99'),D(6),
        CatalystType.NONE,None,True,False,last_price_timestamp=NOW,quote_timestamp=NOW)
    bar=MinuteBar('SPY',NOW-timedelta(minutes=1),D('5.9'),D('6.1'),D('5.8'),D(6),D(1000))
    return PointInTimeObservation(obs,'REGULAR',(bar,),quote_observed_at=NOW,evaluation_timestamp=NOW)


@pytest.fixture
def control(setup,tmp_path,warrior,request):
    commands,bridge,service,account,quotes,clock=setup
    root=tmp_path/'trial';create(root,clock=lambda:NOW,profile=getattr(request,'param','GROK_WARRIOR_V5'))
    path,spec=load(root/'experiment.json',clock=lambda:NOW)
    exp=PaperExperiment(path,spec,clock=lambda:clock[0],timer=lambda:(clock[0]-NOW).total_seconds())
    exp.capture_ready=lambda:True
    sidecar=NS(grok_native_execution_permitted=lambda:True,_account_source=lambda:account[0])
    from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
    sidecar.strategy_config=WarriorMomentumConfig()
    client=Mock();client.usage.return_value={};client.diagnostics.return_value={};client.request.return_value=dict(action='BUY',symbol='SPY',entry='6',stop='5.9',target='6.2',quantity=10,reason='Synthetic plan')
    def request(*args,before_transport):
        before_transport()
        return client.request.return_value
    client.request.side_effect=request
    c=WarriorControl(sidecar,bridge,service,exp,SymbolOwnershipRegistry(),client,clock=lambda:clock[0])
    # No background model call in fixture; direct decisions are driven explicitly.
    with c._lock:c._contexts['SPY']=(point(),NOW)
    yield c
    c.stop();c.producer.request_stop()


def apply(control,warrior,answer=None,event='case'):
    answer=answer or dict(action='BUY',symbol='SPY',entry='6',stop='5.9',target='6.2',quantity=10,reason='Synthetic')
    control._apply(warrior,event,NOW,{'SPY':(point(),NOW)},dict(control._positions),answer)


def test_director_entry_crosses_real_controller_then_native_fill_and_protection(setup,warrior,control):
    apply(control,warrior)
    commands=setup[0]
    buys=[o for o in commands.order_book.history() if o.request.side.value=='BUY']
    assert len(buys)==1
    assert buys[0].request.metadata['provenance']=='GROK_DIRECTED_PAPER'
    assert 'GROK_DIRECTED' in buys[0].request.strategy_lifecycle_id
    assert 'SPY' in warrior._paper
    apply(control,warrior)  # same model event cannot buy twice
    fill(commands)
    assert warrior.reconcile_authoritative_protection('SPY',NOW)
    stops=[o for o in commands.order_book.open_orders_for_symbol('SPY') if o.request.execution_reason=='STOP']
    assert len(stops)==1 and stops[0].request.stop_price==D('5.9') and stops[0].request.quantity==10


@pytest.mark.parametrize('change',['stale','halt','unknown_halt','other_symbol','risk','ownership','paused'])
def test_own_plan_cannot_bypass_current_execution_guards(setup,warrior,control,change):
    answer=dict(action='BUY',symbol='SPY',entry='6',stop='5.9',target='6.2',quantity=10,reason='Synthetic')
    if change=='stale':setup[-1][0]=NOW+timedelta(seconds=11)
    elif change=='halt':control._contexts['SPY']=(replace(point(),observation=replace(point().observation,halted=True)),NOW)
    elif change=='unknown_halt':control._contexts['SPY']=(replace(point(),halt_state_known=False),NOW)
    elif change=='other_symbol':answer['symbol']='QQQ'
    elif change=='risk':answer['stop']='5'
    elif change=='ownership':control.ownership.acquire('SPY',StrategyOwner.QUICK_SCALPER,'QUICK_SCALPER|SPY|owned')
    else:control.root.joinpath('STOP_GROK').touch()
    try:apply(control,warrior,answer)
    except ValueError:pass
    assert not setup[0].order_book.history()


def test_warrior_only_binding_leaves_scalper_callbacks_disconnected(setup,tmp_path,control):
    sidecar,scalper=desktop(setup,tmp_path,control.producer)
    assert scalper._paper_entry_reviewer is None
    assert sidecar._grok_native_producer is control.producer
    assert not control.producer.scalper_offer(None,1,D(1)).pending


def test_shared_request_cap_preserves_eight_director_calls_after_twelve_native_reviews(control):
    from app.asset_modules.grok import GrokClient
    from tests.asset_modules.test_grok import response
    sent=[];clock=[100]
    control.client=GrokClient('fixture-key','grok-test',transport=lambda req:sent.append(req) or response({}),clock=lambda:clock[0])
    native=ControlClient(control,'NATIVE_REVIEW');direct=ControlClient(control,'DIRECTOR')
    for i in range(12):
        clock[0]+=61;native.request('MASTER','fixture',{'mode':'PAPER','event_id':str(i)}, {})
    clock[0]+=61
    with pytest.raises(GrokUnavailable,match='STAGE'):native.request('MASTER','fixture',{'mode':'PAPER','event_id':'extra'}, {})
    for i in range(8):
        clock[0]+=61;direct.request('WARRIOR_MOMENTUM_V1','fixture',{'mode':'PAPER','event_id':str(i)}, {})
    assert len(sent)==20
    clock[0]+=61
    with pytest.raises(GrokUnavailable):direct.request('WARRIOR_MOMENTUM_V1','fixture',{'mode':'PAPER','event_id':'extra'}, {})
    assert control.budget.report()['reserved_attempts']==20
    with pytest.raises(GrokUnavailable,match='WARRIOR_ONLY'):direct.request('QUICK_SCALPER','fixture',{}, {})


def test_director_exit_is_warrior_owned_and_does_not_disable_stop_protection(setup,warrior,control):
    apply(control,warrior);fill(setup[0]);warrior.reconcile_authoritative_protection('SPY',NOW)
    state=warrior._paper['SPY']
    from app.strategies.warrior_momentum.autonomous_paper import lifecycle_identity
    identity=lifecycle_identity(state.signal)
    control._positions={'SPY':{'quantity':10,'lifecycle_id':identity}}
    apply(control,warrior,dict(action='EXIT',symbol='SPY',entry='0',stop='0',target='0',quantity=0,reason='Synthetic exit'),event='exit')
    orders=setup[0].order_book.history()
    assert any(o.request.execution_reason=='GROK_EXIT' and o.request.order_type.value=='MARKET' for o in orders)


def test_director_sees_completed_market_data_without_legacy_pattern_and_logs_once(control):
    control._review_director();control._review_director()
    assert control.client.request.call_count==1
    context=control.client.request.call_args.args[2]
    assert context['event_id']=='director-hour-0'
    assert context['mode']=='PAPER' and context['asset']=='EQUITY'
    assert len(context['market']['SPY']['completed_bars'])==1
    assert 'native_setup_required' not in context
    assert control.budget.report()['reserved_attempts']==1
    control._write_status()
    status=json.loads((control.root/'warrior-control-status.json').read_text())
    assert status['scalper_mode']=='NATIVE' and status['warrior_mode']=='GROK_NATIVE_REVIEW_AND_DIRECTED'


def test_halt_revocation_clears_a_previously_acceptable_context(control,warrior):
    p=replace(point(),observation=replace(point().observation,halted=True))
    control.offer(p,warrior)
    assert 'SPY' not in control._contexts


def test_stop_grok_does_not_stop_native_paper_experiment(control):
    assert control.experiment.active()
    (control.root/'STOP_GROK').touch()
    assert not control.active() and control.experiment.active()


def test_explicit_native_scalper_freshness_does_not_inherit_grok_warrior_deadline(setup):
    from tests.asset_modules.test_controlled_paper_service import placement
    commands,bridge,service,account,quotes,clock=setup
    service.native_scalper_quote_age_seconds=5
    service.router.controller.native_scalper_quote_age_seconds=5
    clock[0]=NOW+timedelta(seconds=3)
    assert service.place_order(placement(commands,engine='QUICK_SCALPER')).success
    rejected=service.place_order(placement(commands,engine='WARRIOR_MOMENTUM_V1',symbol='QQQ'))
    assert not rejected.success and service.last_reason=='PROVIDER_QUOTE_NOT_CURRENT'


@pytest.mark.parametrize('profile',['GROK_WARRIOR_V5','GROK_WARRIOR_ACCOUNT_V6'])
@pytest.mark.parametrize('extended',[False,True])
def test_real_desktop_profile_binds_only_warrior_and_starts_no_model_before_operator_start(tmp_path,monkeypatch,historical_intelligence_artifact,extended,profile):
    from app.configuration import load_configuration
    import app.composition.desktop as desktop_module
    import app.asset_modules.grok_warrior_control as control_module
    from tests.test_support.session_clock import create_session_paper_composition
    from app.equity_overhaul.paper_experiment import ENV
    root=tmp_path/'desktop-trial';create(root,profile=profile,extended_sessions=extended)
    monkeypatch.setenv(ENV,str(root/'experiment.json'))
    config=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','LIVE_TRADING_ENABLED':'false',
        'ATLAS_DAILY_RESEARCH_ENABLED':'true','WARRIOR_FORWARD_PAPER_ENABLED':'true','QUICK_SCALPER_ENABLED':'true',
        'ATLAS_PAPER_CONTROLLER_ENABLED':'true','ATLAS_GROK_PAPER_ENTRIES_ENABLED':'false',
        'WARRIOR_FORWARD_CAPTURE_PATH':str(tmp_path/'capture.sqlite3')})
    monkeypatch.setattr(desktop_module,'load_configuration',lambda:config)
    client=Mock();client.model='grok-4.7'
    monkeypatch.setattr(control_module,'configured_client',lambda *a,**kw:client)
    graph=desktop_module.create_desktop_composition(paper_persistence_path=tmp_path/'paper.sqlite3')
    try:
        c=graph.warrior_forward_sidecar._grok_warrior_control
        assert c is not None and not c.active()
        assert c.account_sizing==(profile=='GROK_WARRIOR_ACCOUNT_V6')
        assert graph.quick_scalper_runtime.risk_config==graph.warrior_forward_sidecar.strategy_config.risk
        assert (graph.quick_scalper_runtime.risk_config.maximum_quantity>10)==c.account_sizing
        assert ('AFTER_HOURS' in graph.warrior_forward_sidecar.strategy_config.entry.allowed_sessions)==extended
        assert ('AFTER_HOURS' in graph.quick_scalper_runtime.config.allowed_sessions)==extended
        assert ('AFTER_HOURS' in c.entry_sessions)==extended
        assert graph.quick_scalper_runtime._paper_entry_reviewer is None
        assert graph.paper_controller_service.native_scalper_quote_age_seconds==5
        assert not graph.equity_paper_experiment.grok_execution
        client.request.assert_not_called()
        assert not c._thread
    finally:graph.close(timeout_seconds=1)


def test_manual_control_launcher_is_explicit_and_has_no_duplicate_observer_launcher(tmp_path,monkeypatch):
    from app.asset_modules.paper_day_setup import prepare
    monkeypatch.setenv('XAI_API_KEY','fixture-secret-only')
    monkeypatch.setenv('ATLAS_GROK_MODEL','grok-4.7')
    repo=tmp_path/'repo';repo.mkdir();root=tmp_path/'trial'
    result=prepare(repo,root,profile='GROK_WARRIOR_V5')
    assert result['shared_warrior_paid_cap']==20 and result['scalper_control']=='NATIVE'
    assert not (root/'Start-Grok-Intentions.ps1').exists()
    assert (root/'Show-Morning-Status.ps1').exists()
    text=(root/'Start-Atlas-Paper-Trial.ps1').read_text()
    assert "'ATLAS_PAPER_CONTROLLER_ENABLED' = 'true'" in text
    assert "'ATLAS_GROK_PAPER_ENTRIES_ENABLED' = 'false'" in text
    assert 'GROK WARRIOR CONTROL ON' in text and 'GROK ORDERS OFF' not in text
    assert '-m app.gui.app' in text and 'fixture-secret-only' not in text
    assert not (root/'experiment.json').exists()


def test_native_warrior_approval_uses_shared_budget_and_canonical_controller(setup,warrior,control):
    from tests.asset_modules.test_grok_runtime_handoff import signal,AUTH,FloatProvenance
    from tests.asset_modules.test_grok_native_review import finished
    from tests.asset_modules.test_grok_paper_entries import suite
    from tests.asset_modules.test_native_review_branches import arm
    ai,_=suite();control.client=ai.client
    value=signal();arm(warrior,value)
    receipt=control.producer.warrior_offer(value,10,D(1),float_provenance=FloatProvenance.UNKNOWN,
        symbol_authorization=AUTH,evaluated_at=NOW)
    assert receipt.pending
    finished(control.producer.review)
    control.producer.drain(warrior,None,at=NOW)
    assert len([o for o in setup[0].order_book.history() if o.request.side.value=='BUY'])==1
    assert control.budget.report()['roles'][0]['stage']=='NATIVE_REVIEW'
    assert ai.client.requests==1
    assert 'SPY' in warrior._paper


def test_preparation_failure_releases_unclaimed_symbol_ownership(control,warrior,monkeypatch,setup):
    def fail(*args,**kwargs):raise ValueError('synthetic preparation failure')
    monkeypatch.setattr(type(setup[1]),'prepare_entry_decision',fail)
    with pytest.raises(ValueError):apply(control,warrior)
    assert control.ownership.owner('SPY') is None
    assert not setup[0].order_book.history()


def test_director_failure_cannot_prevent_native_review_completion_pump(setup,tmp_path,control,monkeypatch):
    sidecar,scalper=desktop(setup,tmp_path,control.producer)
    sidecar._grok_warrior_control=control
    def fail(_):raise RuntimeError('synthetic director fault')
    monkeypatch.setattr(control,'drain',fail)
    native=Mock(return_value=())
    monkeypatch.setattr(control.producer,'drain',native)
    assert sidecar.poll_grok_completions()==()
    native.assert_called_once()
    assert control._events[-1]['status']=='DIRECTOR_DRAIN_FAILED'


def test_stream_context_refresh_does_not_depend_on_native_setup_evaluation(control,warrior,setup,monkeypatch):
    monkeypatch.setattr(control,'_run',lambda:None)
    setup[-1][0]=NOW+timedelta(seconds=5)
    p=point();obs=replace(p.observation,timestamp=setup[-1][0],quote_timestamp=setup[-1][0])
    control.offer_stream(obs,p.bars,'REGULAR',warrior)
    refreshed,at=control._contexts['SPY']
    assert at==setup[-1][0] and refreshed.quote_observed_at==obs.quote_timestamp
    assert refreshed.observation is obs
    assert not warrior._paper
    control.client.request.assert_not_called()


def test_director_real_client_contract_reaches_transport_then_canonical_order(control,warrior,setup):
    from app.asset_modules.grok import GrokClient
    from tests.asset_modules.test_grok import response
    calls=[]
    def transport(request):
        payload=json.loads(request.data)
        context=json.loads(payload['input'][1]['content'])
        assert context['mode']=='PAPER' and context['asset']=='EQUITY'
        assert context['market']['SPY']['provider_quote_at']==NOW.isoformat()
        calls.append(request)
        return response(dict(action='BUY',symbol='SPY',entry='6',stop='5.9',target='6.2',quantity=10,reason='Synthetic'))
    control.client=GrokClient('fixture-key','grok-test',transport=transport,clock=lambda:100)
    control._review_director();control.drain(warrior)
    assert len(calls)==1
    assert control.budget.report()['reserved_attempts']==1
    assert control.client.diagnostics()['answers_parsed']==1
    assert len([o for o in setup[0].order_book.history() if o.request.side.value=='BUY'])==1
    fill(setup[0]);assert warrior.reconcile_authoritative_protection('SPY',NOW)
    assert any(o.request.execution_reason=='STOP' for o in setup[0].order_book.open_orders_for_symbol('SPY'))


@pytest.mark.parametrize('gate',['mode','cooldown','busy','session'])
def test_local_client_rejections_do_not_consume_durable_attempts(control,gate):
    from app.asset_modules.grok import GrokClient
    from tests.asset_modules.test_grok import response
    sent=[]
    control.client=GrokClient('fixture-key','grok-test',transport=lambda request:sent.append(request) or response({}),clock=lambda:100)
    client=ControlClient(control,'NATIVE_REVIEW')
    context={'mode':'PAPER','event_id':'candidate'}
    if gate=='mode':context.pop('mode')
    elif gate=='cooldown':control.client._next['MASTER']=200
    elif gate=='session':control.client.requests=120
    else:
        control.client._slots.acquire();control.client._slots.acquire()
    with pytest.raises((ValueError,GrokUnavailable)):
        client.request('MASTER','fixture',context,{})
    assert control.budget.report()['reserved_attempts']==0
    assert not sent
    assert control._events[-1]['status']=='MODEL_REQUEST_NOT_SENT'


def test_transport_failure_consumes_one_attempt_without_retry(control):
    from app.asset_modules.grok import GrokClient
    sent=[]
    def transport(request):
        sent.append(request)
        raise TimeoutError()
    control.client=GrokClient('fixture-key','grok-test',transport=transport,clock=lambda:100)
    with pytest.raises(GrokUnavailable):
        ControlClient(control,'DIRECTOR').request('WARRIOR_MOMENTUM_V1','fixture',{'mode':'PAPER','event_id':'case'},{})
    report=control.budget.report()
    assert report['reserved_attempts']==1 and report['unfinalized_attempts']==0
    assert len(sent)==1 and control.client.diagnostics()['failures']=={'TIMEOUT':1}


def test_durable_cap_refusal_prevents_transport_and_preserves_client_cooldown(control):
    from app.asset_modules.grok import GrokClient
    sent=[]
    for i in range(8):
        identifier=control.budget.reserve('WARRIOR_MOMENTUM_V1','DIRECTOR',{'event_id':str(i)})
        control.budget.finish(identifier,{'status':'fixture'})
    control.client=GrokClient('fixture-key','grok-test',transport=lambda request:sent.append(request),clock=lambda:100)
    with pytest.raises(GrokUnavailable,match='DURABLE_STAGE_REQUEST_CAP'):
        ControlClient(control,'DIRECTOR').request('WARRIOR_MOMENTUM_V1','fixture',{'mode':'PAPER','event_id':'extra'},{})
    assert not sent and control.client.requests==0 and not control.client._next
    assert control.budget.report()['reserved_attempts']==8


@pytest.mark.parametrize('extended',[False,True])
def test_director_capture_and_dispatch_share_explicit_after_hours_policy(control,warrior,extended,monkeypatch):
    from app.equity_overhaul.paper_experiment import EXTENDED_ENTRY_SESSIONS
    control.entry_sessions=EXTENDED_ENTRY_SESSIONS if extended else frozenset({'PREMARKET','REGULAR'})
    monkeypatch.setattr(control,'_run',lambda:None)
    p=replace(point(),session='AFTER_HOURS')
    control.offer(p,warrior)
    if extended:
        assert control._contexts['SPY'][0].session=='AFTER_HOURS'
        # Revalidation must use the same opted-in session policy.
        apply(control,warrior)
        assert control._events[-1]['status']=='DIRECTOR_ENTRY_ACKNOWLEDGED'
    else:
        assert 'SPY' not in control._contexts


@pytest.mark.parametrize('control',['GROK_WARRIOR_ACCOUNT_V6'],indirect=True)
def test_account_sized_grok_plan_exceeds_small_trial_caps_and_keeps_native_stops(control,warrior,setup):
    answer=dict(action='BUY',symbol='SPY',entry='6',stop='5.9',target='6.2',quantity=100,reason='Synthetic account-sized plan')
    apply(control,warrior,answer)
    buys=[o for o in setup[0].order_book.history() if o.request.side.value=='BUY']
    assert len(buys)==1 and buys[0].request.quantity==D(100)
    fill(setup[0]);assert warrior.reconcile_authoritative_protection('SPY',NOW)
    assert any(o.request.execution_reason=='STOP' and o.request.quantity==D(100) for o in setup[0].order_book.open_orders_for_symbol('SPY'))


@pytest.mark.parametrize('control',['GROK_WARRIOR_ACCOUNT_V6'],indirect=True)
def test_account_sizing_does_not_bypass_current_account_risk(control,warrior,setup):
    # Native per-trade allowance is min($100, 0.5% of $10k) = $50.
    answer=dict(action='BUY',symbol='SPY',entry='6',stop='5.9',target='6.2',quantity=1000,reason='Synthetic excessive risk')
    apply(control,warrior,answer)
    assert not any(o.request.side.value=='BUY' for o in setup[0].order_book.history())
    assert control.service.last_reason=='RISK_NOT_AUTHORIZED'


@pytest.mark.parametrize('control',['GROK_WARRIOR_ACCOUNT_V6'],indirect=True)
def test_director_receives_account_sizing_schema_and_partial_exit_policy(control):
    control._review_director()
    args=control.client.request.call_args.args
    evidence,schema=args[2],args[3]
    assert schema['properties']['quantity']['maximum']==control.service.risk_config.maximum_quantity
    assert schema['properties']['quantity']['maximum']>10
    assert evidence['limits']['maximum_position_dollars'] is None
    assert evidence['position_sizing']['account']['buying_power']=='10000'
    assert evidence['native_management']['configured_partial_exit_fractions']=={'first_target':'0.50','second_target':'0.25','runner':'0.25'}
    assert evidence['native_management']['native_minimum_remaining_reward_r']=={'first_target':'0.5','final_target':'1.5'}


def test_native_review_includes_actual_partial_policy_without_enlarging_candidate_identity(control,warrior,setup):
    from tests.asset_modules.test_grok_runtime_handoff import signal,AUTH,FloatProvenance
    from tests.asset_modules.test_grok_native_review import finished
    from tests.asset_modules.test_grok_paper_entries import suite
    from tests.asset_modules.test_native_review_branches import arm
    ai,_=suite();control.client=ai.client
    value=signal();arm(warrior,value)
    receipt=control.producer.warrior_offer(value,10,D(1),float_provenance=FloatProvenance.UNKNOWN,
        symbol_authorization=AUTH,evaluated_at=NOW)
    assert receipt.pending;finished(control.producer.review)
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(control.budget.path)) as db:
        raw=db.execute('SELECT context FROM calls').fetchone()[0]
    context=json.loads(raw)
    evidence=context['evidence']['native_engine_evidence']['WARRIOR_MOMENTUM_V1']['additional_evidence']
    policy=evidence['native_management']
    assert policy['configured_partial_exit_fractions']['first_target']=='0.50'
    assert policy['native_minimum_remaining_reward_r']['final_target']=='1.5'
    assert context['candidates'][0]['evidence']['target_levels']==[str(p) for p in value.target_levels]


@pytest.mark.parametrize('control',['GROK_WARRIOR_ACCOUNT_V6'],indirect=True)
def test_full_day_director_is_paced_across_24_hours_without_increasing_paid_cap(control,setup):
    spec=control.experiment.spec
    spec['limits']['hours']=24
    spec['expires_at']=(NOW+timedelta(hours=24)).isoformat()
    control.experiment.path.write_text(json.dumps(spec))
    for offset,expected in ((0,0),(2,0),(3,1),(21,7),(23,7)):
        setup[-1][0]=NOW+timedelta(hours=offset)
        with control._lock:control._contexts['SPY']=(point(),setup[-1][0])
        control._review_director()
        assert control._last_hour==expected
    assert control.budget.report()['reserved_attempts']<=8
