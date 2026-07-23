from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from app.analytics import AnalyticsPolicy,AnalyticsRequest,AnalyticsRuntime,DeterministicAnalyticsEvaluator
from app.trade_journal import TradeJournalEntryType,TradeJournalState,TradeJournalStatus
from tests.trade_journal.helpers import cycle,request as journal_request,runtime as journal_runtime
BASE=journal_runtime().append(journal_request()).entry
def entry(index=0,pnl="10",equity="100",starting="80",fees="1",quantity="2",kind=TradeJournalEntryType.EXECUTION):
    shift=timedelta(minutes=index)
    return replace(BASE,entry_id=f"entry-{index}",cycle_id=f"cycle-{index}",recorded_at=BASE.recorded_at+shift,cycle_started_at=BASE.cycle_started_at+shift,cycle_completed_at=BASE.cycle_completed_at+shift,realized_profit_loss=Decimal(pnl) if pnl is not None else None,ending_equity=Decimal(equity) if equity is not None else None,starting_equity=Decimal(starting) if starting is not None else None,equity_change=None,fees=Decimal(fees) if fees is not None else None,filled_quantity=Decimal(quantity) if quantity is not None else None,entry_type=kind)
def journal(entries=(),status=TradeJournalStatus.ACTIVE,metadata=None):return TradeJournalState("journal-1",status,tuple(entries),len(entries),None,metadata or {})
def request(entries=(),starting_equity=None,status=TradeJournalStatus.ACTIVE):
    j=journal(entries,status);last=entries[-1].recorded_at if entries else BASE.recorded_at
    return AnalyticsRequest("analytics-1",j,last+timedelta(minutes=1),{"source":"test"},starting_equity)
def runtime(**policy):return AnalyticsRuntime(DeterministicAnalyticsEvaluator(),AnalyticsPolicy(enabled=True,**policy))
class Evaluator:
    def __init__(self,response=None,error=None):self.response=response;self.error=error;self.calls=[]
    def evaluate(self,request,policy):
        self.calls.append((request,policy))
        if self.error:raise self.error
        return self.response
