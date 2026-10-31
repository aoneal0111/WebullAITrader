"""Use the native quote adapter, not independently invented policy geometry."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import pytest

from app.paper_trading.order_book import PaperOrderBook
from app.strategies.warrior_momentum.configuration import RiskConfig
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig, QuickScalperPolicy, ScalpDecision
from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperPaperRuntimeAdapter
from tests.warrior_momentum.test_quick_scalper_runtime import NOW, Bridge, Quotes, account, quote, stream_observation


def cent_config(*,support=True):
    return QuickScalperConfig(enabled=True,cent_target_minimum=D('.05'),cent_target_maximum=D('.10'),
        minimum_net_target_reward_r=D(1),cent_pullback_support_enabled=support)


def make_runtime(config,quotes=None):
    bridge=Bridge();quotes=quotes or Quotes(replace(quote(),bid=D('4.95'),ask=D('4.96'),last=D('4.96')))
    runtime=QuickScalperPaperRuntimeAdapter(config=config,bridge=bridge,order_book=PaperOrderBook(),
        risk_config=RiskConfig(configured_per_trade_risk=D(1),maximum_quantity=10,maximum_position_dollars=D(100)),
        account_context_source=account,position_quantity_source=lambda _:D(0),execution_quote_source=quotes,clock=lambda:NOW)
    return runtime,bridge,quotes


@pytest.mark.parametrize('move',['.01','.05','.10','.15','.20','.50'])
def test_original_cent_stream_constraints_cannot_approve_native_monotonic_geometry(move):
    runtime,_,_=make_runtime(cent_config(support=False))
    runtime._stream_snapshot(stream_observation('4.80',-10),decision_at=NOW,session='REGULAR',context=None)
    value=runtime._stream_snapshot(stream_observation(str(D('4.80')+D(move)),0),decision_at=NOW,session='REGULAR',context=None)
    result=runtime.scalper.policy.assess(value)
    # min(window BID) <= first BID. Thus structural risk > observed BID
    # advance, yet target >= risk*1R+cost and advance >= target+cost
    # were both required. This is not a missing quote or brokerage rejection.
    assert value.ask-value.structural_stop>value.observed_bid_advance
    assert result.decision is not ScalpDecision.EXECUTABLE


def test_real_native_pullback_can_reach_confirmation_and_submission_without_changing_safety():
    runtime,bridge,quotes=make_runtime(cent_config())
    for bid,offset in [('4.80',-10),('5.00',-6),('4.93',-3)]:
        runtime.observe_stream_event(stream_observation(bid,offset),decision_at=NOW,session='REGULAR',execution_permitted=False)
    result=runtime.observe_stream_event(stream_observation('4.95',0),decision_at=NOW,session='REGULAR')
    assert result.decision is ScalpDecision.EXECUTABLE
    assert quotes.calls==1 and len(bridge.entries)==1
    intent,shares,risk,_=bridge.entries[0]
    assert intent.stop_price==D('4.93')
    assert intent.support_source=='CONFIRMED_MICRO_PULLBACK'
    assert intent.support_timestamp==NOW-timedelta(seconds=3)
    assert intent.short_horizon_range==D('.20')
    assert intent.observed_bid_advance==D('.15')
    assert D('.05')<=intent.cent_target_move<=D('.10')
    assert 0<shares<=10 and shares*intent.entry_trigger<=100 and risk<=1
    assert intent.adaptive_target-intent.entry_trigger-intent.execution_cost>=intent.risk_per_share*intent.required_net_target_reward_r
    runtime.observe_stream_event(stream_observation('4.95',0),decision_at=NOW,session='REGULAR')
    assert len(bridge.entries)==1


def test_continuous_rise_does_not_invent_tight_support_or_force_confirmation():
    runtime,bridge,quotes=make_runtime(cent_config())
    for bid,offset in [('4.80',-10),('4.90',-6),('4.95',0)]:
        result=runtime.observe_stream_event(stream_observation(bid,offset),decision_at=NOW,session='REGULAR')
    assert result.reason=='CENT_SUPPORT_FORMING'
    assert quotes.calls==0 and not bridge.entries
    current=runtime._snapshots[('FAST',result.opportunity.generation_id)]
    assert current.structural_stop==D('4.80') and current.support_timestamp is None


def test_support_requires_later_distinct_provider_quote_and_is_invalidated_on_touch():
    runtime,_,_=make_runtime(cent_config())
    for bid,offset in [('4.80',-10),('5.00',-6),('4.93',-3)]:
        value=runtime._stream_snapshot(stream_observation(bid,offset),decision_at=NOW,session='REGULAR',context=None)
    assert value.support_source=='WINDOW_LOW'
    # Replacing a quote at the same provider instant cannot confirm its old low.
    same=runtime._stream_snapshot(stream_observation('4.95',-3),decision_at=NOW,session='REGULAR',context=None)
    assert same.support_source=='WINDOW_LOW'
    # A later bounce confirms the actual low of the replaced provider sample.
    formed=runtime._stream_snapshot(stream_observation('4.97',-1),decision_at=NOW,session='REGULAR',context=None)
    assert formed.structural_stop==D('4.95') and formed.support_timestamp==NOW-timedelta(seconds=3)
    touched=runtime._stream_snapshot(stream_observation('4.95',0),decision_at=NOW,session='REGULAR',context=None)
    assert touched.support_source=='WINDOW_LOW'


@pytest.mark.parametrize('bad', ['stale','future','below_support','unavailable'])
def test_confirmed_support_never_bypasses_authoritative_quote_checks(bad):
    q=replace(quote(),bid=D('4.95'),ask=D('4.96'),last=D('4.96'))
    if bad=='stale':q=replace(q,bid_timestamp=NOW-timedelta(seconds=6),ask_timestamp=NOW-timedelta(seconds=6))
    elif bad=='future':q=replace(q,bid_timestamp=NOW+timedelta(seconds=1))
    elif bad=='below_support':q=replace(q,bid=D('4.92'),ask=D('4.94'),last=D('4.94'))
    else:q=None
    runtime,bridge,quotes=make_runtime(cent_config(),Quotes(q))
    for bid,offset in [('4.80',-10),('5.00',-6),('4.93',-3)]:
        runtime.observe_stream_event(stream_observation(bid,offset),decision_at=NOW,session='REGULAR',execution_permitted=False)
    runtime.observe_stream_event(stream_observation('4.95',0),decision_at=NOW,session='REGULAR')
    assert quotes.calls==1 and not bridge.entries


def test_too_wide_actual_pullback_risk_keeps_cent_economics_wait():
    runtime,bridge,quotes=make_runtime(cent_config())
    for bid,offset in [('4.50',-10),('5.00',-6),('4.80',-3)]:
        runtime.observe_stream_event(stream_observation(bid,offset),decision_at=NOW,session='REGULAR',execution_permitted=False)
    result=runtime.observe_stream_event(stream_observation('4.95',0),decision_at=NOW,session='REGULAR')
    assert result.reason=='CENT_TARGET_ECONOMICS_WAIT'
    assert quotes.calls==0 and not bridge.entries


def test_normal_scalper_config_keeps_original_window_low():
    runtime,_,_=make_runtime(QuickScalperConfig(enabled=True))
    for bid,offset in [('4.80',-10),('5.00',-6),('4.93',-3),('4.95',0)]:
        value=runtime._stream_snapshot(stream_observation(bid,offset),decision_at=NOW,session='REGULAR',context=None)
    assert value.structural_stop==D('4.80') and value.short_horizon_range==D('.20')
    assert value.support_source=='WINDOW_LOW' and value.support_timestamp is None


def test_support_flag_requires_cent_bounds():
    with pytest.raises(ValueError):QuickScalperConfig(cent_pullback_support_enabled=True)


@pytest.mark.parametrize('cent',[False,True])
def test_new_entry_support_does_not_reprice_existing_position(cent):
    from tests.warrior_momentum.test_cent_scalper_profile import seeded
    original,_=seeded(cent=cent)
    prior=original._positions['FAST'];bridge=Bridge()
    restored=QuickScalperPaperRuntimeAdapter(config=cent_config(),bridge=bridge,order_book=original.order_book,
        account_context_source=account,position_quantity_source=lambda _:D(10),execution_quote_source=Quotes(quote()),clock=lambda:NOW)
    state=restored._positions['FAST']
    assert state.initial_stop==prior.initial_stop and state.target_price==prior.target_price
    assert state.entry_price==prior.entry_price and not bridge.entries


@pytest.mark.parametrize('fill_anchored',[False,True])
def test_native_stream_to_durable_gateway_fill_protection_and_cent_exit(tmp_path,fill_anchored):
    from app.market_data.models import MarketEvent,MarketEventType,QuotePayload
    from app.paper_trading.order_models import OrderSide
    from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
    from tests.test_support.session_clock import create_session_paper_composition
    position={'qty':D(0)}
    def sink(event):
        if event.fill is not None:
            position['qty']+=event.fill.quantity*(1 if event.fill.side=='BUY' else -1)
    c=create_session_paper_composition(at=NOW,event_sink=sink,position_quantity_source=lambda _:position['qty'],
        persistence_path=tmp_path/'cent-stream.sqlite3')
    try:
        bridge=AutonomousPaperExecutionBridge(c.trading_service,c.order_command_factory,mode='PAPER',enabled=True,
            order_book=c.order_book,protection_amender=c.gateway.amend_protective_stop,position_quantity_source=lambda _:position['qty'])
        quotes=Quotes(replace(quote(),bid=D('4.95'),ask=D('4.96'),last=D('4.96')))
        runtime=QuickScalperPaperRuntimeAdapter(config=replace(cent_config(),cent_fill_anchored_economics=fill_anchored),bridge=bridge,order_book=c.order_book,
            risk_config=RiskConfig(configured_per_trade_risk=D(1),maximum_quantity=10,maximum_position_dollars=D(100)),
            account_context_source=account,position_quantity_source=lambda _:position['qty'],execution_quote_source=quotes,clock=lambda:NOW)
        for bid,offset in [('4.80',-10),('5.00',-6),('4.93',-3)]:
            runtime.observe_stream_event(stream_observation(bid,offset),decision_at=NOW,session='REGULAR',execution_permitted=False)
        runtime.observe_stream_event(stream_observation('4.95',0),decision_at=NOW,session='REGULAR')
        entries=[o for o in c.order_book.history() if o.request.side is OrderSide.BUY]
        assert len(entries)==1
        entry=entries[0]
        assert entry.request.metadata['scalp_support_source']=='CONFIRMED_MICRO_PULLBACK'
        assert entry.request.metadata['scalp_support_timestamp']==(NOW-timedelta(seconds=3)).isoformat()
        c.gateway.process_market_event(MarketEvent(1,NOW+timedelta(seconds=1),'FAST','test',MarketEventType.QUOTE,
            QuotePayload(D('4.95'),D('4.96'),D(100),D(100))))
        runtime.reconcile('FAST')
        assert D(0)<position['qty']<=10
        working=c.order_book.open_orders_for_symbol('FAST')
        assert next(o.request.stop_price for o in working if o.request.execution_reason=='STOP')==D('4.93')
        target=next(o.request.limit_price for o in working if o.request.execution_reason=='SCALP_TARGET')
        assert target==D('5.06')
        c.gateway.process_market_event(MarketEvent(2,NOW+timedelta(seconds=2),'FAST','test',MarketEventType.QUOTE,
            QuotePayload(target,target+D('.01'),D(100),D(100))))
        runtime.reconcile('FAST')
        assert position['qty']==0
        exits=[o for o in c.durable_store.orders() if o.request.side is OrderSide.SELL and o.filled_quantity>0]
        assert len(exits)==1 and exits[0].request.execution_reason=='SCALP_TARGET'
        assert exits[0].average_fill_price>=target
    finally:c.close()
