from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from unittest.mock import Mock
from types import SimpleNamespace
import pytest

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.paper_trading.command_composition import create_paper_trading_command_composition
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousPaperExecutionBridge, PaperExitSubmissionDecision,
    PaperExitSubmissionState, PaperExitSubmissionFailureReason)
from app.strategies.warrior_momentum.quick_scalper_runtime import (
    QuickScalperExecutionIntent, QuickScalperPaperRuntimeAdapter)
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig
from tests.warrior_momentum.test_quick_scalper_runtime import account, Quotes, quote


@pytest.fixture
def partial_stop():
    clock=[datetime(2026,11,12,15,tzinfo=UTC)];holder={}
    def sink(event):
        if 'runtime' in holder:holder['runtime'].observe_paper_event(event)
    commands=create_paper_trading_command_composition(clock=lambda:clock[0],event_sink=sink,
        position_quantity_source=lambda _:D(0))
    bridge=AutonomousPaperExecutionBridge(commands.trading_service,commands.order_command_factory,
        order_book=commands.order_book,protection_amender=commands.gateway.amend_protective_stop,
        position_quantity_source=lambda _:D(0))
    runtime=QuickScalperPaperRuntimeAdapter(config=QuickScalperConfig(enabled=True),bridge=bridge,
        order_book=commands.order_book,account_context_source=account,position_quantity_source=lambda _:D(0),
        execution_quote_source=Quotes(quote()),clock=lambda:clock[0])
    holder['runtime']=runtime;n=clock[0];identity='QUICK_SCALPER|PART|synthetic-stop'
    intent=QuickScalperExecutionIntent(symbol='PART',timestamp=n,session='REGULAR',entry_trigger=D('2.75'),
        reference_price=D('2.75'),stop_price=D('2.70'),risk_per_share=D('.05'),adaptive_target=D('2.85'),
        lifecycle_id=identity,opportunity_id='op',generation_id='g',authorization_id='auth',execution_quote_id='q',
        execution_quote_timestamp=n,provider_last_timestamp=n,provider_bid_timestamp=n,provider_ask_timestamp=n)
    assert bridge.submit_entry_decision(intent,10,D('.5'),opportunity_id='op',
        opportunity_anchor=D('2.75'),provenance='QUICK_SCALPER_CANONICAL_ENTRY').authorized
    sequence=[0]
    def tick(bid,ask,size):
        clock[0]+=timedelta(seconds=1);sequence[0]+=1
        commands.gateway.process_market_event(MarketEvent(sequence[0],clock[0],'PART','webull',
            MarketEventType.QUOTE,QuotePayload(D(bid),D(ask),D(size),D(10))))
    tick('2.74','2.75',10)
    tick('2.69','2.70',4)
    try:yield commands,runtime,tick
    finally:commands.close()


def test_partial_triggered_stop_is_progress_and_survives_rebound_without_replacement(partial_stop):
    commands,runtime,tick=partial_stop
    working=commands.order_book.open_orders_for_symbol('PART')
    stop=next(o for o in working if o.request.order_type.value=='STOP')
    assert stop.request.metadata['stop_triggered'] is True and stop.remaining_quantity==6
    count=len(commands.order_book.history())
    for _ in range(5):runtime.reconcile('PART')
    status=runtime.execution_status()
    assert status['exit_failures']=={}
    assert status['exit_progress']=={'TRIGGERED_STOP_WORKING':1}
    assert status['owned_positions'][0]['protected_quantity']=='6'
    tick('2.71','2.72',3) # Rebound must continue the already-triggered liquidation.
    remaining=commands.order_book.open_orders_for_symbol('PART')
    assert len(remaining)==1 and remaining[0].order_id==stop.order_id and remaining[0].remaining_quantity==3
    assert len(commands.order_book.history())==count
    assert runtime.execution_status()['exit_progress']['TRIGGERED_STOP_WORKING']==2
    tick('2.68','2.69',3)
    runtime.reconcile('PART')
    assert not commands.order_book.open_orders_for_symbol('PART') and not runtime._positions
    assert sum(o.filled_quantity for o in commands.order_book.history() if o.side.value=='SELL')==10
    assert runtime.execution_status()['exit_failures']=={}


@pytest.mark.parametrize('mode',['undercovered','overcovered','foreign_owner'])
def test_conflicting_or_wrong_coverage_is_still_reported_as_failure(partial_stop,mode):
    commands,runtime,_=partial_stop;state=runtime._positions['PART']
    qty=7 if mode=='undercovered' else 5 if mode=='overcovered' else 6
    if mode=='foreign_owner':
        real=commands.order_book.open_orders_for_symbol('PART')
        foreign=SimpleNamespace(request=SimpleNamespace(strategy_lifecycle_id=state.lifecycle_id,
            execution_reason='STOP',side=next(o.request.side for o in real),order_type=next(o.request.order_type for o in real),
            metadata={'strategy_owner':'OTHER','stop_triggered':True}))
        runtime.order_book=SimpleNamespace(open_orders_for_symbol=lambda symbol:[foreign])
    refusal=PaperExitSubmissionDecision(PaperExitSubmissionState.UNAVAILABLE,'PART',state.lifecycle_id,'STOP',
        failure_reason=PaperExitSubmissionFailureReason.DUPLICATE_OR_CONFLICTING_EXIT)
    runtime.bridge=SimpleNamespace(ensure_exit=Mock(return_value=refusal))
    runtime._ensure_protection(state,qty)
    runtime.bridge.ensure_exit.assert_called_once()
    assert runtime._exit_failure_reasons['STOP|DUPLICATE_OR_CONFLICTING_EXIT']==1
