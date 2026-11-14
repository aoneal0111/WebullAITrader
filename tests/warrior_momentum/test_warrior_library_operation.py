"""All implemented families reach native gates; unavailable families never gain authority."""
from dataclasses import replace
from decimal import Decimal as D
import json
from types import SimpleNamespace as NS
import pytest

from app.equity_overhaul.paper_experiment import (create, load, PaperExperiment,
    ACCOUNT_SIZING_PROFILE, WARRIOR_LIBRARY_POLICY, warrior_library_families)
from app.equity_overhaul.warrior_library_repair import migrate
from app.opportunity_discovery import (ACTIVE_STRATEGY_ORDER, DetectionState,
    MultiStrategyDiscoveryEngine, normalize_detections)
from app.opportunity_discovery.taxonomy import STRATEGY_TAXONOMY
from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
from app.strategies.warrior_momentum.models import SetupState
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig
from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.runtime import WarriorMomentumRuntime
from app.trade_intelligence.taxonomy_paper_bridge import TaxonomyPaperExecutionBridge, _discovery_context
from tests.warrior_momentum.test_forward_capture import point, account, T0


def operation(tmp_path):
    create(tmp_path,clock=lambda:T0,profile=ACCOUNT_SIZING_PROFILE,continuous=True,
           extended_sessions=True,clock_tolerance=True)
    path,spec=load(tmp_path/'experiment.json');return PaperExperiment(path,spec)


def test_migration_preserves_scalper_operation_limits_and_identity(tmp_path):
    old=operation(tmp_path);before=old.path.read_bytes();report=migrate(tmp_path)
    path,spec=load(old.path);new=PaperExperiment(path,spec)
    assert report['status']=='WARRIOR_LIBRARY_PREPARED'
    assert spec['trial_id']==old.spec['trial_id'] and spec['limits']==old.spec['limits']
    assert new.scalper_config(QuickScalperConfig())==old.scalper_config(QuickScalperConfig())
    previous=old.warrior_config(WarriorMomentumConfig());current=new.warrior_config(WarriorMomentumConfig())
    assert current.taxonomy_paper_execution_enabled and current.setups.allowed_setup_types is None
    assert current.entry==previous.entry and current.risk==previous.risk
    assert current.trade_management==previous.trade_management
    assert spec['families']['WARRIOR_MOMENTUM']==warrior_library_families()
    assert len(ACTIVE_STRATEGY_ORDER)==23 and len(STRATEGY_TAXONOMY)==30
    assert set(ACTIVE_STRATEGY_ORDER)<=set(spec['families']['WARRIOR_MOMENTUM'])
    assert (tmp_path / report['spec_backup']).read_bytes()==before
    assert migrate(tmp_path)['status']=='ALREADY_CONFIGURED'
    # Exact known policy, not arbitrary family additions or live permission.
    spec['families']['WARRIOR_MOMENTUM'].append('HALT_RESUMPTION_CONTINUATION')
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError):load(path)


@pytest.mark.parametrize('strategy', ACTIVE_STRATEGY_ORDER)
def test_each_family_keeps_identity_and_still_passes_native_entry_gates(strategy):
    value=point();runtime=WarriorMomentumRuntime();candidate=runtime.discover(value.observation,value.bars,session=value.session)
    bridge=TaxonomyPaperExecutionBridge();context,_=_discovery_context(value)
    batch=MultiStrategyDiscoveryEngine().observe(context)
    base=next(x for x in batch.detections if x.state is DetectionState.DETECTED)
    detection=replace(base,strategy_id=strategy)
    # Contract integration: detector geometry already independently covered by
    # the detector suites. Feed one family at a time to prove none is relabeled
    # or lost behind the former three-family permission list.
    bridge.discovery=NS(observe=lambda _:NS(lifecycle_detections=(detection,)))
    projected=bridge.select_paper_candidate(value,candidate,5)
    signal=runtime.entry_signal(projected)
    assert signal is not None and signal.taxonomy_strategy_id==strategy
    assert projected.setup.score==D(90) and signal.execution_authorized is False
    assert runtime.entry_signal(replace(projected,halted=True)) is None
    assert runtime.entry_signal(replace(projected,tradable=False)) is None
    assert runtime.entry_signal(replace(projected,setup=replace(projected.setup,stop_price=D(0),trigger=D(0)))) is None
    # A rejected/AI-waiting proposal has not consumed its execution identity.
    assert bridge.select_paper_candidate(value,candidate,5).setup.taxonomy_execution_identity==projected.setup.taxonomy_execution_identity


def test_old_detected_membership_cannot_override_current_invalidation():
    value=point();runtime=WarriorMomentumRuntime();candidate=runtime.discover(value.observation,value.bars,session=value.session)
    bridge=TaxonomyPaperExecutionBridge();context,_=_discovery_context(value)
    batch=bridge.discovery.observe(context)
    bridge.discovery=NS(observe=lambda _:NS(lifecycle_detections=tuple(
        replace(x,state=DetectionState.INVALIDATED) for x in batch.detections),
        opportunities=batch.opportunities))
    assert bridge.select_paper_candidate(value,candidate,5) is candidate


def test_real_detector_routes_before_canonical_quote_account_and_risk_path(tmp_path,monkeypatch):
    store=ForwardCaptureStore(tmp_path/'capture.sqlite3');writer=ForwardCaptureWriter(store,flush_interval_seconds=.01)
    config=replace(WarriorMomentumConfig(),taxonomy_paper_execution_enabled=True)
    service=WarriorForwardCaptureService(store,writer,config=config,
        taxonomy_execution_bridge=TaxonomyPaperExecutionBridge())
    original=service.runtime.discover
    def no_native(*args,**kwargs):
        c=original(*args,**kwargs)
        return replace(c,setup=replace(c.setup,state=SetupState.NOT_FORMED,score=D(10)))
    monkeypatch.setattr(service.runtime,'discover',no_native)
    try:
        candidate,signal=service.observe(point(),account=account())
        assert candidate.setup.taxonomy_strategy_id in ACTIVE_STRATEGY_ORDER
        assert signal is not None and signal.taxonomy_strategy_id in ACTIVE_STRATEGY_ORDER
        assert signal.execution_authorized is False  # no broker/Grok bypass
    finally:writer.close()


def test_every_family_passes_source_bound_permission_with_original_name(tmp_path):
    from app.asset_modules.equity_policy_attribution import EquityPolicyAttribution
    from tests.equity_overhaul.test_account_paper_sizing import placement
    old=operation(tmp_path);migrate(tmp_path);path,spec=load(old.path)
    exp=PaperExperiment(path,spec);exp.capture_ready=lambda:True
    attribution=EquityPolicyAttribution({'WARRIOR_MOMENTUM_V1':'b'*64,'QUICK_SCALPER':'d'*64})
    exp.bind(attribution)
    for index,name in enumerate(ACTIVE_STRATEGY_ORDER):
        signal=NS(strategy_id='WARRIOR_MOMENTUM_V1',taxonomy_strategy_id=name,setup_type=NS(value='HIGH_OF_DAY_BREAKOUT'))
        metadata=attribution(signal)
        assert metadata['strategy_family']==name
        request=placement(exp,str(index));request.order.metadata.update(metadata)
        assert exp.claim_order(request),name
        assert not exp.claim_order(request)  # durable request ownership
    request=placement(exp,'unimplemented');request.order.metadata['strategy_family']='VWAP_RECLAIM'
    assert not exp.claim_order(request)
    request=placement(exp,'tampered');request.order.metadata['strategy_code_fingerprint']='e'*64
    assert not exp.claim_order(request)
