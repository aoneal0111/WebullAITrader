from decimal import Decimal as D

import pytest

from app.strategies.warrior_momentum.adaptive_exit import AdaptiveExitEvidence
from app.strategies.warrior_momentum.autonomous_paper import (
    PaperExitSubmissionDecision, PaperExitSubmissionState,
    PaperExitSubmissionFailureReason,
)
from tests.warrior_momentum.test_durable_target_reconciliation import _harness, NOW, LIFECYCLE


def failed_working(role='STOP'):
    # Bridge uses WORKING plus failure when an old/triggered exit is retained.
    return PaperExitSubmissionDecision(PaperExitSubmissionState.WORKING,'XRPN',
        LIFECYCLE,role,'retained-old-order',NOW,
        PaperExitSubmissionFailureReason.DUPLICATE_OR_CONFLICTING_EXIT)


def arm(service):
    state=service._paper['XRPN']
    state.protection_reconciled=True
    state.peak_executable_bid=D('18.60')
    state.peak_executable_r=(D('18.60')-state.entry_price)/(state.entry_price-state.stop)
    state.latest_exit_evidence=AdaptiveExitEvidence(NOW,True,D('18.50'),D('18.51'))
    return state


def test_working_failure_preserves_existing_protection_but_is_not_new_request_success():
    result=failed_working()
    assert result.protection_active  # Preserve existing broad safety meaning.
    assert not result.requested_order_active
    assert not result.request_succeeded


def test_failed_peak_lock_keeps_confirmed_stop_and_retries(tmp_path):
    composition,bridge,submitter,writer,service,position=_harness(tmp_path)
    try:
        state=arm(service);state.pending_profit_harvest_role='PROFIT_HARVEST_1'
        state.active_exit_role='PROFIT_HARVEST_1';state.active_exit_order_id='retained-target'
        before=state.stop;calls=[]
        def rejected(*args,**kwargs):
            calls.append(args);return failed_working(args[3])
        service._paper_exit_submitter=rejected
        for _ in range(2):
            service._manage_executable_profit_harvest(state,NOW)
            assert state.stop==before
            assert not state.profit_defense_stop_tightened
            assert state.active_exit_order_id=='retained-target'
        assert len(calls)==2  # Failed amendment remains retryable.
        service._paper_exit_submitter=submitter.submit
        assert service._manage_executable_profit_harvest(state,NOW)
        assert state.stop>before and state.profit_defense_stop_tightened
        stops=[order for order in composition.order_book.open_orders_for_symbol('XRPN')
            if order.request.order_type.value=='STOP']
        assert stops and all(order.request.stop_price>=state.stop for order in stops)
    finally:writer.close()


def test_failed_harvest_does_not_latch_pending_or_overwrite_fill_role(tmp_path):
    composition,bridge,submitter,writer,service,position=_harness(tmp_path)
    try:
        state=arm(service);state.active_exit_role='FIRST_TARGET';state.active_exit_order_id='actual-target'
        service._paper_exit_submitter=lambda *args,**kwargs:failed_working(args[3])
        service._manage_executable_profit_harvest(state,NOW)
        assert state.pending_profit_harvest_role is None
        assert state.active_exit_role=='FIRST_TARGET'
        assert state.active_exit_order_id=='actual-target'
    finally:writer.close()


@pytest.mark.parametrize('result',[False,None])
def test_false_legacy_submit_response_does_not_replace_active_fill_attribution(tmp_path,result):
    composition,bridge,submitter,writer,service,position=_harness(tmp_path)
    try:
        state=service._paper['XRPN'];state.active_exit_role='FIRST_TARGET';state.active_exit_order_id='actual-target'
        service._paper_exit_submitter=lambda *args,**kwargs:result
        service._submit_exit(state,D('18.50'),5,'PROFIT_HARVEST_1')
        assert (state.active_exit_role,state.active_exit_order_id)==('FIRST_TARGET','actual-target')
    finally:writer.close()


@pytest.mark.parametrize('state',list(PaperExitSubmissionState))
@pytest.mark.parametrize('failure',[None,PaperExitSubmissionFailureReason.CANCELLATION_FAILED])
def test_request_acknowledgement_distinguishes_state_and_failure(state,failure):
    result=PaperExitSubmissionDecision(state,'XRPN',LIFECYCLE,'STOP',failure_reason=failure)
    assert result.requested_order_active == (failure is None and state in {
        PaperExitSubmissionState.SUBMITTED,PaperExitSubmissionState.WORKING})
    assert result.request_succeeded == (failure is None and state in {
        PaperExitSubmissionState.SUBMITTED,PaperExitSubmissionState.WORKING,PaperExitSubmissionState.COMPLETED})


@pytest.mark.parametrize('full_exit',[False,True])
def test_scalper_rejected_defense_is_retryable_and_never_reported_active(monkeypatch,full_exit):
    from datetime import timedelta
    from types import SimpleNamespace
    from app.paper_trading.order_models import OrderSide,OrderType
    from app.strategies.warrior_momentum.profit_retention import ProfitState
    from app.strategies.warrior_momentum.quick_scalper_runtime import _ScalpPositionState
    from tests.warrior_momentum.test_quick_scalper_runtime import adapter,NOW as SCALP_NOW
    events=[];runtime,bridge,quotes=adapter(events=events)
    runtime.position_quantity_source=lambda symbol:D(10)
    request=SimpleNamespace(strategy_lifecycle_id='life',side=OrderSide.SELL,
        order_type=OrderType.STOP,stop_price=D('4.9'))
    stop=SimpleNamespace(request=request,remaining_quantity=D(10))
    monkeypatch.setattr(runtime.order_book,'open_orders_for_symbol',lambda symbol:(stop,))
    state=_ScalpPositionState('FAST','life','g1',D(5),D('4.9'),D('5.2'),
        SCALP_NOW-timedelta(seconds=301 if full_exit else 10),D('5.1'),ProfitState.PROFIT_ESTABLISHED)
    runtime._positions['FAST']=state
    calls=[]
    def rejected(*args,**kwargs):
        calls.append(args)
        return PaperExitSubmissionDecision(PaperExitSubmissionState.WORKING,'FAST','life',args[3],
            failure_reason=PaperExitSubmissionFailureReason.DUPLICATE_OR_CONFLICTING_EXIT)
    original_submit=bridge.ensure_exit
    monkeypatch.setattr(bridge,'ensure_exit',rejected)
    marked=[];monkeypatch.setattr(runtime.engine,'mark_exit_requested',lambda *args,**kwargs:marked.append(args))
    for _ in range(2):
        runtime._manage_open_position_values(symbol='FAST',executable_bid=D('5.07'),
            quote_timestamp=SCALP_NOW,observed_at=SCALP_NOW,momentum_stalled=False)
        assert state.profit_state is ProfitState.PROFIT_ESTABLISHED
    assert len(calls)==2 and not marked
    assert not any(event in {'SCALP_PROFIT_DEFENSE','SCALP_EXIT_REQUESTED'} for event,_ in events)
    monkeypatch.setattr(bridge,'ensure_exit',original_submit)
    runtime._manage_open_position_values(symbol='FAST',executable_bid=D('5.07'),
        quote_timestamp=SCALP_NOW,observed_at=SCALP_NOW,momentum_stalled=False)
    assert state.profit_state is ProfitState.PROFIT_DEFENSE_ACTIVE
    expected='SCALP_EXIT_REQUESTED' if full_exit else 'SCALP_PROFIT_DEFENSE'
    assert any(event==expected for event,_ in events)


def test_real_bridge_cancel_failure_keeps_old_stop_without_claiming_new_target(tmp_path,monkeypatch):
    composition,bridge,submitter,writer,service,position=_harness(tmp_path)
    try:
        before=tuple(composition.order_book.open_orders_for_symbol('XRPN'))
        monkeypatch.setattr(type(bridge),'_cancel_working_order',lambda self,order:False)
        result=bridge.ensure_exit('XRPN',5,D('18.50'),'PROFIT_HARVEST_1',LIFECYCLE)
        assert result.state is PaperExitSubmissionState.WORKING
        assert result.failure_reason is PaperExitSubmissionFailureReason.CANCELLATION_FAILED
        assert result.protection_active and not result.request_succeeded
        assert tuple(composition.order_book.open_orders_for_symbol('XRPN'))==before
        assert not any(order.request.execution_reason=='PROFIT_HARVEST_1'
            for order in composition.order_book.history())
    finally:writer.close()


@pytest.mark.parametrize('method,expected',[
    ('manage_session_boundary','SESSION_CLOSE_UNAVAILABLE'),
    ('flatten_for_overnight_capability_loss','OVERNIGHT_CAPABILITY_EXIT_UNAVAILABLE'),
])
def test_failed_boundary_exit_is_not_reported_submitted(tmp_path,monkeypatch,method,expected):
    from types import SimpleNamespace
    composition,bridge,submitter,writer,service,position=_harness(tmp_path)
    try:
        monkeypatch.setattr('app.strategies.warrior_momentum.forward_runtime.flatten_window_reached',lambda *args:True)
        monkeypatch.setattr('app.strategies.warrior_momentum.forward_runtime.assess_overnight_carry',lambda **kwargs:SimpleNamespace(carry=False))
        state=service._paper['XRPN'];state.active_exit_role='FIRST_TARGET';state.active_exit_order_id='actual-target'
        service._paper_exit_submitter=lambda *args,**kwargs:failed_working(args[3])
        assert getattr(service,method)(NOW)==(('XRPN',expected),)
        assert state.active_exit_order_id=='actual-target'
    finally:writer.close()
