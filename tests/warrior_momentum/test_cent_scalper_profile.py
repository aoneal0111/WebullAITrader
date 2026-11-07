from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from app.market_data.models import MarketEvent,MarketEventType,QuotePayload
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import OrderSide,OrderStatus
from app.performance_diagnostics import performance_diagnostics
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig,QuickScalperPolicy,ScalpDecision
from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperPaperRuntimeAdapter,QuickScalperExecutionIntent
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from tests.test_support.session_clock import create_session_paper_composition,session_timestamp
from tests.warrior_momentum.test_quick_scalper import snapshot
from tests.warrior_momentum.test_quick_scalper_runtime import Bridge,Quotes,quote,account,paper_order,NOW


def config():
    return QuickScalperConfig(enabled=True,cent_target_minimum=D('.05'),cent_target_maximum=D('.10'),
        minimum_net_target_reward_r=D(1))


@pytest.mark.parametrize('short_range',['.12','.2','.5','1'])
def test_cent_economics_use_bounded_targets_not_unbounded_volatility(short_range):
    result=QuickScalperPolicy(config()).assess(snapshot(ask='5',bid='4.99',stop='4.98',short_range=short_range))
    assert D('.05')<=result.target_move<=D('.10')
    assert result.target_move==result.target_move.quantize(D('.01'))
    assert result.decision is ScalpDecision.EXECUTABLE
    assert result.opportunity.remaining_final_target_r>=D(1)


@pytest.mark.parametrize('ask,bid,stop',[('10.81','10.75','10.40'),('9.75','9.74','9.60'),('5','4.90','4.98')])
def test_risk_or_cost_cannot_be_hidden_by_stretching_target_or_stop(ask,bid,stop):
    value=snapshot(ask=ask,bid=bid,stop=stop,short_range='1')
    result=QuickScalperPolicy(config()).assess(value)
    assert result.decision is ScalpDecision.WAIT and result.reason=='CENT_TARGET_ECONOMICS_WAIT'
    assert result.target_move==D('.10') and value.structural_stop==D(stop)


def test_cent_target_selects_viable_whole_cent_inside_band_before_rejecting():
    # $0.10 of demonstrated movement, a $0.04 structural risk and $0.03
    # modeled costs need a $0.07 target, not the old $0.06 cost-only choice.
    value=snapshot(ask='2.23',bid='2.22',stop='2.19',short_range='.10')
    result=QuickScalperPolicy(config()).assess(value)
    assert result.target_move==D('.07')
    assert result.decision is ScalpDecision.EXECUTABLE
    assert value.structural_stop==D('2.19')
    assert result.target_move-result.round_trip_cost>=D('.04')*result.required_net_target_reward_r


def test_selected_cent_target_covers_confidence_penalty_without_rounding_down():
    value=replace(snapshot(ask='2.23',bid='2.22',stop='2.19',short_range='.10'),
        stream_sample_count=14,stream_elapsed_seconds=D('29.382'),
        stream_upward_updates=3,stream_price_change_updates=4)
    result=QuickScalperPolicy(config()).assess(value)
    floor=D('.04')*result.required_net_target_reward_r+result.round_trip_cost
    assert D('.07')<floor<=D('.08')
    assert result.target_move==D('.08')
    assert result.decision is ScalpDecision.EXECUTABLE


def test_recorded_ncpl_geometry_still_waits_for_demonstrated_move_after_band_fix():
    # Geometry from the Oct 30 summary, without claiming a full journal replay.
    value=replace(snapshot(ask='2.23',bid='2.22',stop='2.19',short_range='.03'),
        observed_bid_advance=D('.03'),observed_ask_advance=D('.01'),
        stream_sample_count=14,stream_elapsed_seconds=D('29.382'),
        stream_upward_updates=3,stream_price_change_updates=4,
        setup_quality=D('40.72'),momentum_priority=D('42.39667757416570326220816877'),
        relative_volume=D('.3806327980435806739198170528'),catalyst_context='FALSE')
    result=QuickScalperPolicy(config()).assess(value)
    assert result.target_move==D('.08')
    assert result.decision is ScalpDecision.WAIT
    assert result.reason=='INSUFFICIENT_NET_EXECUTABLE_EDGE'
    assert result.expected_move==D('.01')


def test_freshness_and_price_safety_still_override_cent_economics():
    value=snapshot(ask='5',bid='4.99',stop='4.98',short_range='.2')
    assert QuickScalperPolicy(config()).assess(replace(value,bid_timestamp=NOW-timedelta(seconds=6))).decision is ScalpDecision.REJECTED_HARD_SAFETY
    with pytest.raises(ValueError):replace(config(),cent_target_maximum=D('.11'))
    with pytest.raises(ValueError):replace(config(),cent_target_minimum=D('.055'))
    with pytest.raises(ValueError):replace(config(),cent_target_maximum=None)


def seeded(*,cent=True,legacy_config=False,tap=None):
    book=PaperOrderBook();entry=paper_order(order_id='entry',side=OrderSide.BUY,quantity='10',filled='10',
        status=OrderStatus.FILLED,reason='ENTRY')
    if cent:
        entry=replace(entry,request=replace(entry.request,metadata={**entry.request.metadata,'scalp_cent_target_move':'.06'}),
            average_fill_price=D('4.99'))
    book.submit(entry);bridge=Bridge()
    runtime=QuickScalperPaperRuntimeAdapter(config=QuickScalperConfig(enabled=True) if legacy_config else config(),
        bridge=bridge,order_book=book,account_context_source=account,position_quantity_source=lambda _:D(10),
        execution_quote_source=Quotes(quote()),clock=lambda:NOW,observer_context_tap=tap)
    return runtime,bridge


def test_cent_target_anchors_to_actual_fill_and_survives_adapter_restart():
    runtime,bridge=seeded()
    assert bridge.exits[-1][2:4]==(D('5.05'),'SCALP_TARGET')
    again,restored=seeded(legacy_config=True)
    assert restored.exits[-1][2:4]==(D('5.05'),'SCALP_TARGET')
    old,old_bridge=seeded(cent=False)
    assert old_bridge.exits[-1][2:4]==(D('5.20'),'SCALP_TARGET')


def test_cent_position_does_not_cancel_limit_for_stall_but_max_hold_still_exits(tmp_path):
    from app.asset_modules.grok_observer_capture import ContextCapture
    from app.asset_modules.grok_observer_runtime_tap import RuntimeContextTap
    records=[]
    capture=ContextCapture(tmp_path/'capture','a'*64,clock=lambda:NOW)
    tap=RuntimeContextTap(capture,'a'*64,journal=NS(submit=lambda kind,data:records.append(data)))
    runtime,bridge=seeded(tap=tap);bridge.exits.clear()
    runtime._manage_open_position_values(symbol='FAST',executable_bid=D('5.05'),quote_timestamp=NOW,
        observed_at=NOW,momentum_stalled=True)
    assert not any(e[3]=='SCALP_MOMENTUM_STALL' for e in bridge.exits)
    later=NOW+timedelta(seconds=301)
    runtime._manage_open_position_values(symbol='FAST',executable_bid=D('4.99'),quote_timestamp=later,
        observed_at=later,momentum_stalled=True)
    assert bridge.exits[-1][3]=='SCALP_MAX_HOLD'
    tap.close()
    assert records[-1]['decision_bid']==D('4.99') and records[-1]['provider_bid_timestamp']==later


def test_real_gateway_fills_cent_limit_after_price_improvement_and_keeps_stop(tmp_path):
    qty={'value':D(0)}
    def sink(event):
        if event.fill is not None:qty['value']+=event.fill.quantity*(1 if event.fill.side=='BUY' else -1)
    stamp=session_timestamp()
    c=create_session_paper_composition(event_sink=sink,position_quantity_source=lambda _:qty['value'],persistence_path=tmp_path/'paper.sqlite3')
    try:
        bridge=AutonomousPaperExecutionBridge(c.trading_service,c.order_command_factory,mode='PAPER',enabled=True,
            order_book=c.order_book,protection_amender=c.gateway.amend_protective_stop,position_quantity_source=lambda _:qty['value'])
        intent=QuickScalperExecutionIntent(symbol='FAST',timestamp=stamp,session='REGULAR',entry_trigger=D('5'),reference_price=D('5'),
            stop_price=D('4.97'),risk_per_share=D('.03'),adaptive_target=D('5.06'),cent_target_move=D('.06'),
            lifecycle_id='cent-life',opportunity_id='cent-opportunity',generation_id='g',authorization_id='auth',execution_quote_id='quote',
            execution_quote_timestamp=stamp,provider_last_timestamp=stamp,provider_bid_timestamp=stamp,provider_ask_timestamp=stamp)
        decision=bridge.submit_entry_decision(intent,10,D('.30'))
        assert decision.authorized
        c.gateway.process_market_event(MarketEvent(1,session_timestamp(1),'FAST','test',MarketEventType.QUOTE,
            QuotePayload(D('4.98'),D('4.99'),D(100),D(100))))
        runtime=QuickScalperPaperRuntimeAdapter(config=config(),bridge=bridge,order_book=c.order_book,account_context_source=account,
            position_quantity_source=lambda _:qty['value'],execution_quote_source=Quotes(quote()),clock=lambda:stamp)
        exits=c.order_book.open_orders_for_symbol('FAST')
        assert next(o.request.limit_price for o in exits if o.request.execution_reason=='SCALP_TARGET')==D('5.05')
        assert next(o.request.stop_price for o in exits if o.request.execution_reason=='STOP')==D('4.97')
        entry=next(o for o in c.durable_store.orders() if o.request.side is OrderSide.BUY)
        assert entry.request.metadata['scalp_cent_target_move']=='0.06'
        # Reopen the actual SQLite ledger and restore the adapter with the
        # ordinary config: the position's persisted exit contract still wins.
        c.close();qty['value']=D(0)
        c=create_session_paper_composition(event_sink=sink,position_quantity_source=lambda _:qty['value'],
            persistence_path=tmp_path/'paper.sqlite3')
        bridge=AutonomousPaperExecutionBridge(c.trading_service,c.order_command_factory,mode='PAPER',enabled=True,
            order_book=c.order_book,protection_amender=c.gateway.amend_protective_stop,position_quantity_source=lambda _:qty['value'])
        bridge.reconcile()
        runtime=QuickScalperPaperRuntimeAdapter(config=QuickScalperConfig(enabled=True),bridge=bridge,order_book=c.order_book,
            account_context_source=account,position_quantity_source=lambda _:qty['value'],execution_quote_source=Quotes(quote()),clock=lambda:stamp)
        assert runtime._positions['FAST'].target_price==D('5.05')
        assert len([o for o in c.order_book.history() if o.request.side is OrderSide.BUY])==1
        c.gateway.process_market_event(MarketEvent(2,session_timestamp(2),'FAST','test',MarketEventType.QUOTE,
            QuotePayload(D('5.05'),D('5.06'),D(100),D(100))))
        runtime.reconcile('FAST')
        assert qty['value']==0
        sells=[o for o in c.order_book.history() if o.request.side is OrderSide.SELL and o.filled_quantity>0]
        assert len(sells)==1 and sells[0].request.execution_reason=='SCALP_TARGET'
        assert sells[0].average_fill_price>=D('5.05')
    finally:c.close()
