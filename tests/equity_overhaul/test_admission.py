from dataclasses import dataclass
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.equity_overhaul.admission import (EquityQualificationPolicy,QualifiedEquityTradingService,
    Promotion,production_policy)
from app.services.order_command_factory import OrderEntryCommand
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from tests.test_support.session_clock import create_session_paper_composition


def placement(factory,side='BUY',metadata=None):
    return factory.create_placement_request(OrderEntryCommand(symbol='PMI',side=side,quantity=D(10),
        order_type='LIMIT',limit_price=D(10),stop_price=None,time_in_force='DAY',strategy_lifecycle_id='WARRIOR_MOMENTUM_V1|PMI|x',
        metadata=metadata or {'source':'autonomous-paper','reason':'ENTRY'}))


@pytest.mark.parametrize('identity',['WARRIOR_MOMENTUM_V1','QUICK_SCALPER','ORB5_LONG_V1','NOISE_BAND_LONG_V1','VWAP_RECLAIM_SCALP_V1','UNKNOWN'])
def test_no_legacy_or_claimed_replacement_can_self_authorize(identity):
    c=create_session_paper_composition()
    try:
        underlying=Mock();p=production_policy();service=QualifiedEquityTradingService(underlying,p)
        r=service.place_order(placement(c.order_command_factory,metadata={
            'source':'autonomous-paper','reason':'ENTRY','strategy_policy_version':identity,
            'strategy_code_fingerprint':'a'*64,'strategy_configuration_fingerprint':'b'*64,
            'win_rate':'99.9','approved':'true'}))
        assert not r.success and r.gateway_message=='STRATEGY_NOT_QUALIFIED'
        underlying.place_order.assert_not_called()
        assert p.status()['approved_policies']==[]
    finally:c.close()


def test_sell_and_cancellation_pass_through_when_all_buys_quarantined():
    c=create_session_paper_composition()
    try:
        original=Mock();wrapped=QualifiedEquityTradingService(original,production_policy())
        sell=placement(c.order_command_factory,'SELL')
        assert wrapped.place_order(sell) is original.place_order.return_value
        cancel=object()
        assert wrapped.cancel_order(cancel) is original.cancel_order.return_value
    finally:c.close()


def test_registry_requires_exact_reviewed_rule_and_config_and_report_hashes():
    with pytest.raises(ValueError):Promotion('QUICK_SCALPER','a'*64,'b'*64,'c'*64)
    with pytest.raises(ValueError):Promotion('ORB5_LONG_V1','not-a-hash','b'*64,'c'*64)
    p=EquityQualificationPolicy((Promotion('ORB5_LONG_V1','a'*64,'b'*64,'c'*64),))
    m=dict(strategy_policy_version='ORB5_LONG_V1',strategy_owner='WARRIOR_MOMENTUM',strategy_code_fingerprint='a'*64,
           strategy_configuration_fingerprint='b'*64)
    assert p.permits(m)
    assert not p.permits({**m,'strategy_code_fingerprint':'d'*64})
    assert not p.permits({**m,'strategy_configuration_fingerprint':'d'*64})
    assert not p.permits({'approved':True})


@dataclass(frozen=True)
class Signal:
    symbol:str='PMI'
    entry_trigger:D=D(10)
    stop_price:D=D(9)
    lifecycle_id:str='WARRIOR_MOMENTUM_V1|PMI|x'


@pytest.mark.parametrize('prepare',[False,True])
def test_bridge_refuses_before_preparation_order_construction_or_ai_review(prepare):
    service=Mock();factory=Mock()
    bridge=AutonomousPaperExecutionBridge(service,factory,entry_qualification=lambda _:False)
    result=(bridge.prepare_entry_decision if prepare else bridge.submit_entry_decision)(Signal(),10,D(10))
    decision=result.decision if prepare else result
    assert decision.reason.value=='STRATEGY_NOT_QUALIFIED'
    assert not decision.authorized
    service.place_order.assert_not_called();factory.create_placement_request.assert_not_called()


def test_inherited_working_buy_cancelled_and_sell_retained():
    c=create_session_paper_composition()
    try:
        for side in ('BUY','SELL'):assert c.trading_service.place_order(placement(c.order_command_factory,side)).success
        wrapped=QualifiedEquityTradingService(c.trading_service,production_policy())
        assert wrapped.quarantine_working_buys(c.order_book,c.order_command_factory)
        working=c.order_book.open_orders()
        assert len(working)==1 and working[0].request.side.value=='SELL'
    finally:c.close()


@pytest.mark.parametrize('controller',['true','false'])
def test_actual_desktop_always_installs_qualification_for_warrior_scalper_and_direct_dispatch(monkeypatch,tmp_path,controller):
    import app.composition.desktop as desktop
    from app.configuration import load_configuration
    configuration=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER',
        'WARRIOR_FORWARD_PAPER_ENABLED':'true','QUICK_SCALPER_ENABLED':'true',
        'ATLAS_PAPER_CONTROLLER_ENABLED':controller,
        'WARRIOR_FORWARD_CAPTURE_PATH':str(tmp_path/'forward.sqlite3'),'ALLOWED_SYMBOLS':'PMI'})
    monkeypatch.setattr(desktop,'load_configuration',lambda:configuration)
    composition=desktop.create_desktop_composition(paper_persistence_path=tmp_path/'paper.sqlite3')
    try:
        bridge=composition.autonomous_paper_bridge
        assert bridge is not None and bridge.entry_qualification is not None
        result=bridge.submit_entry_decision(Signal(),10,D(10))
        assert result.reason.value=='STRATEGY_NOT_QUALIFIED'
        # Direct dispatch cannot bypass the early bridge check.
        result=bridge.trading_service.place_order(placement(bridge.order_command_factory))
        assert not result.success
        assert composition.paper_order_book.history()==()
    finally:composition.close(timeout_seconds=1)


def test_uncertain_inherited_buy_cancellation_is_reported_without_touching_sells():
    request=SimpleNamespace(side=SimpleNamespace(value='BUY'),metadata={},client_order_id='client')
    order=SimpleNamespace(request=request,order_id='order')
    book=SimpleNamespace(open_orders=lambda:(order,))
    underlying=Mock();underlying.cancel_order.return_value=SimpleNamespace(success=False)
    wrapped=QualifiedEquityTradingService(underlying,production_policy())
    assert not wrapped.quarantine_working_buys(book,Mock())
    underlying.place_order.assert_not_called()


def test_entry_qualification_exception_fails_closed():
    def broken(_):raise RuntimeError('synthetic unavailable evidence')
    bridge=AutonomousPaperExecutionBridge(Mock(),Mock(),entry_qualification=broken)
    assert bridge.submit_entry_decision(Signal(),10,D(10)).reason.value=='STRATEGY_NOT_QUALIFIED'


@pytest.mark.parametrize('policy,owner,wrong',[
    ('ORB5_LONG_V1','WARRIOR_MOMENTUM','QUICK_SCALPER'),
    ('NOISE_BAND_LONG_V1','WARRIOR_MOMENTUM','QUICK_SCALPER'),
    ('VWAP_RECLAIM_SCALP_V1','QUICK_SCALPER','WARRIOR_MOMENTUM'),
])
def test_release_approval_is_bound_to_its_own_engine(policy,owner,wrong):
    p=EquityQualificationPolicy((Promotion(policy,'a'*64,'b'*64,'c'*64),))
    m=dict(strategy_policy_version=policy,strategy_owner=owner,
        strategy_code_fingerprint='a'*64,strategy_configuration_fingerprint='b'*64)
    assert p.permits(m)
    assert not p.permits({**m,'strategy_owner':wrong})
    assert not p.permits({k:v for k,v in m.items() if k!='strategy_owner'})
    from app.equity_overhaul.admission import lifecycle_matches_owner
    assert lifecycle_matches_owner(owner+'|PMI|one',owner)
    assert not lifecycle_matches_owner(wrong+'|PMI|one',owner)
    assert not lifecycle_matches_owner(owner+'|PMI|',owner)


def test_final_service_denies_correct_approval_with_other_engines_lifecycle():
    c=create_session_paper_composition()
    try:
        p=EquityQualificationPolicy((Promotion('VWAP_RECLAIM_SCALP_V1','a'*64,'b'*64,'c'*64),))
        original=Mock();wrapped=QualifiedEquityTradingService(original,p)
        request=placement(c.order_command_factory,metadata=dict(strategy_policy_version='VWAP_RECLAIM_SCALP_V1',
            strategy_owner='QUICK_SCALPER',strategy_code_fingerprint='a'*64,strategy_configuration_fingerprint='b'*64))
        assert not wrapped.place_order(request).success
        original.place_order.assert_not_called()
        # Approved scalper metadata cannot preserve a wrongly owned inherited BUY either.
        order=SimpleNamespace(request=request.order,order_id='synthetic')
        original.cancel_order.return_value=SimpleNamespace(success=True)
        assert wrapped.quarantine_working_buys(SimpleNamespace(open_orders=lambda:(order,)),Mock())
        original.cancel_order.assert_called_once()
    finally:c.close()
