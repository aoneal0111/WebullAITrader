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
def control(setup,tmp_path,warrior):
    commands,bridge,service,account,quotes,clock=setup
    root=tmp_path/'trial';create(root,clock=lambda:NOW,profile='GROK_WARRIOR_V5')
    path,spec=load(root/'experiment.json',clock=lambda:NOW)
    exp=PaperExperiment(path,spec,clock=lambda:clock[0],timer=lambda:(clock[0]-NOW).total_seconds())
    exp.capture_ready=lambda:True
    sidecar=NS(grok_native_execution_permitted=lambda:True,_account_source=lambda:account[0])
    client=Mock();client.usage.return_value={};client.diagnostics.return_value={};client.request.return_value=dict(action='BUY',symbol='SPY',entry='6',stop='5.9',target='6.2',quantity=10,reason='Synthetic plan')
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
    native=ControlClient(control,'NATIVE_REVIEW');direct=ControlClient(control,'DIRECTOR')
    control.client.request.return_value={}
    for i in range(12):native.request('MASTER','fixture',{'event_id':str(i)}, {})
    with pytest.raises(GrokUnavailable,match='STAGE'):native.request('MASTER','fixture',{'event_id':'extra'}, {})
    for i in range(8):direct.request('WARRIOR_MOMENTUM_V1','fixture',{'event_id':str(i)}, {})
    assert control.client.request.call_count==20
    with pytest.raises(GrokUnavailable):direct.request('WARRIOR_MOMENTUM_V1','fixture',{'event_id':'extra'}, {})
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


def test_real_desktop_profile_binds_only_warrior_and_starts_no_model_before_operator_start(tmp_path,monkeypatch,historical_intelligence_artifact):
    from app.configuration import load_configuration
    import app.composition.desktop as desktop_module
    import app.asset_modules.grok_warrior_control as control_module
    from tests.test_support.session_clock import create_session_paper_composition
    from app.equity_overhaul.paper_experiment import ENV
    root=tmp_path/'desktop-trial';create(root,profile='GROK_WARRIOR_V5')
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
