from datetime import datetime,UTC,timedelta
from decimal import Decimal as D
from app.market_data.models import MarketEvent,MarketEventType,QuotePayload
from app.paper_trading.command_composition import create_paper_trading_command_composition
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperExecutionIntent,QuickScalperPaperRuntimeAdapter
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig
from tests.warrior_momentum.test_quick_scalper_runtime import account,Quotes,quote


def test_partial_18_then_57_protects_and_exits_all_75_despite_stale_zero_projection():
    clock=[datetime(2026,11,10,20,21,23,tzinfo=UTC)];holder={}
    # Deliberately stale GUI position source for the entire test.
    def sink(event):
        if 'runtime' in holder:holder['runtime'].observe_paper_event(event)
    c=create_paper_trading_command_composition(clock=lambda:clock[0],event_sink=sink,position_quantity_source=lambda _:D(0))
    b=AutonomousPaperExecutionBridge(c.trading_service,c.order_command_factory,order_book=c.order_book,
        protection_amender=c.gateway.amend_protective_stop,position_quantity_source=lambda _:D(0))
    r=QuickScalperPaperRuntimeAdapter(config=QuickScalperConfig(enabled=True),bridge=b,order_book=c.order_book,
        account_context_source=account,position_quantity_source=lambda _:D(0),execution_quote_source=Quotes(quote()),clock=lambda:clock[0])
    holder['runtime']=r;n=clock[0];identity='QUICK_SCALPER|PART|synthetic-partial'
    intent=QuickScalperExecutionIntent(symbol='PART',timestamp=n,session='REGULAR',entry_trigger=D('2.75'),
        reference_price=D('2.75'),stop_price=D('2.70'),risk_per_share=D('.05'),adaptive_target=D('2.85'),
        lifecycle_id=identity,opportunity_id='op',generation_id='g',authorization_id='auth',execution_quote_id='q',
        execution_quote_timestamp=n,provider_last_timestamp=n,provider_bid_timestamp=n,provider_ask_timestamp=n)
    assert b.submit_entry_decision(intent,75,D('4.5'),opportunity_id='op',opportunity_anchor=D('2.75'),provenance='QUICK_SCALPER_CANONICAL_ENTRY').authorized
    try:
        for seq,qty,total in [(1,18,18),(2,57,75)]:
            clock[0]+=timedelta(seconds=1)
            c.gateway.process_market_event(MarketEvent(seq,clock[0],'PART','webull',MarketEventType.QUOTE,
                QuotePayload(D('2.74'),D('2.75'),D(300),D(qty))))
            stops=[o for o in c.order_book.open_orders_for_symbol('PART') if o.request.order_type.value=='STOP']
            assert len(stops)==1 and stops[0].remaining_quantity==total
        from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperReadiness
        b.management_context_source=lambda _: 'NON_OWNING_WARRIOR_ANALYTIC_CONTEXT'
        assert b.reconcile() is AutonomousPaperReadiness.READY
        clock[0]+=timedelta(seconds=301)
        r._manage_open_position_values(symbol='PART',executable_bid=D('2.76'),quote_timestamp=clock[0],observed_at=clock[0],momentum_stalled=False)
        exits=[o for o in c.order_book.open_orders_for_symbol('PART') if o.request.execution_reason=='SCALP_MAX_HOLD']
        assert len(exits)==1 and exits[0].remaining_quantity==75
        clock[0]+=timedelta(seconds=1)
        c.gateway.process_market_event(MarketEvent(3,clock[0],'PART','webull',MarketEventType.QUOTE,
            QuotePayload(D('2.76'),D('2.77'),D(75),D(75))))
        r.reconcile('PART')
        assert sum(o.filled_quantity for o in c.order_book.history() if o.side.value=='SELL')==75
        assert not c.order_book.open_orders_for_symbol('PART')
        assert not r._positions
    finally:c.close()
