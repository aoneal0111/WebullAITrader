from typing import Protocol
from app.trade_journal import TradeJournalAppendRequest,TradeJournalAppendResult,TradeJournalState
from app.trading_cycle import TradingCycle
class TradeJournalAppender(Protocol):
    def append(self,request:TradeJournalAppendRequest)->TradeJournalAppendResult:...
class TradeJournalBatchAppendRequestFactory(Protocol):
    def create(self,batch_request,cycle:TradingCycle,current_journal:TradeJournalState,index:int)->TradeJournalAppendRequest:...
