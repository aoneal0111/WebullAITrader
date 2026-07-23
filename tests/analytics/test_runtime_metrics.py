from decimal import Decimal
import pytest
from app.analytics import *
from app.trade_journal import TradeJournalEntryType
from tests.analytics.helpers import entry,request,runtime

def history():return (entry(0,"10","100","80","1","2",TradeJournalEntryType.EXECUTION),entry(1,"-4","90",None,"2","3",TradeJournalEntryType.PARTIAL_EXECUTION),entry(2,"0","110",None,None,None,TradeJournalEntryType.NO_ACTION),entry(3,None,None,None,"0","0",TradeJournalEntryType.REJECTION))

def test_counts_classification_rates_and_profit_formulas():
    m=runtime().evaluate(request(history())).summary.metrics
    assert (m.total_entries,m.executed_entries,m.partial_execution_entries,m.no_action_entries,m.rejected_entries)==(4,1,1,1,1)
    assert (m.classified_trades,m.winning_trades,m.losing_trades,m.breakeven_trades,m.unclassified_trades)==(3,1,1,1,1)
    assert (m.win_rate,m.loss_rate,m.breakeven_rate)==(Decimal(1)/3,Decimal(1)/3,Decimal(1)/3)
    assert m.gross_profit==10 and m.gross_loss==-4 and m.net_profit==6 and m.average_trade==2
    assert m.average_winner==10 and m.average_loser==-4 and m.largest_winner==10 and m.largest_loser==-4
    assert m.profit_factor==Decimal("2.5") and m.expectancy==2

def test_execution_aggregates_skip_missing_and_keep_supplied_zero():
    m=runtime().evaluate(request(history())).summary.metrics
    assert m.total_fees==3 and m.total_filled_quantity==5 and m.average_filled_quantity==Decimal(5)/3

def test_zero_unclassified_policy():
    m=runtime(classify_zero_realized_profit_loss_as_breakeven=False).evaluate(request((entry(pnl="0"),))).summary.metrics
    assert m.classified_trades==0 and m.unclassified_trades==1 and m.win_rate is None and m.net_profit is None

def test_breakeven_only_sign_conventions():
    m=runtime().evaluate(request((entry(pnl="0"),))).summary.metrics
    assert m.gross_profit==0 and m.gross_loss==0 and m.net_profit==0 and m.average_trade==0
    assert m.profit_factor is None and m.average_winner is None and m.average_loser is None

def test_no_classified_trades_optional_profit_metrics_none():
    m=runtime().evaluate(request((entry(pnl=None),))).summary.metrics
    for name in ("gross_profit","gross_loss","net_profit","average_trade","profit_factor","expectancy","win_rate"):assert getattr(m,name) is None

def test_all_category_counts_independent_of_classification():
    kinds=tuple(TradeJournalEntryType);entries=tuple(entry(i,None,kind=k) for i,k in enumerate(kinds));m=runtime().evaluate(request(entries)).summary.metrics
    assert m.total_entries==6 and m.unclassified_trades==6
    assert (m.executed_entries,m.partial_execution_entries,m.no_action_entries,m.rejected_entries,m.failed_entries,m.disabled_entries)==(1,1,1,1,1,1)

def test_request_starting_equity_precedence_and_equity_metrics():
    m=runtime().evaluate(request(history(),starting_equity="70")).summary.metrics
    assert m.starting_equity==70 and m.ending_equity==110 and m.maximum_equity==110 and m.minimum_equity==90 and m.equity_change==40

def test_journal_starting_equity_fallback():assert runtime().evaluate(request(history())).summary.metrics.starting_equity==80

def test_no_supplied_fees_or_quantities_returns_none():
    m=runtime().evaluate(request((entry(fees=None,quantity=None),))).summary.metrics
    assert m.total_fees is None and m.total_filled_quantity is None and m.average_filled_quantity is None
