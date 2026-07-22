from dataclasses import replace
from datetime import timedelta
from app.trade_journal import *
from app.trading_cycle import TradingCycleBuilder,TradingCyclePolicy
from tests.trading_cycle.helpers import build_request
NOW=build_request().completed_at+timedelta(minutes=1)
def cycle(**kwargs):
    value=TradingCycleBuilder(TradingCyclePolicy(enabled=True)).build(build_request(**kwargs)).cycle
    return value
def state(entries=(),summary=None,journal_id="journal-1",status=TradeJournalStatus.ACTIVE):return TradeJournalState(journal_id,status,tuple(entries),len(entries),summary)
def request(c=None,s=None,entry_id="entry-1",journal_id="journal-1",recorded_at=NOW):return TradeJournalAppendRequest(journal_id,entry_id,c or cycle(),s or state(),recorded_at,{"source":"test"})
def runtime(evaluator=None,**policy):return TradeJournalRuntime(TradeJournalPolicy(enabled=True,**policy),evaluator)
class Evaluator:
    def __init__(self,response=None,error=None):self.response=response;self.error=error;self.calls=[]
    def evaluate(self,state,entry,policy):
        self.calls.append((state,entry,policy))
        if self.error:raise self.error
        return self.response
