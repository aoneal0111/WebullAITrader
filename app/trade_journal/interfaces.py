from typing import Protocol
from app.trade_journal.models import TradeJournalEntry,TradeJournalState,TradeJournalSummary
from app.trade_journal.policies import TradeJournalPolicy
class TradeJournalSummaryEvaluator(Protocol):
    def evaluate(self,state:TradeJournalState,entry:TradeJournalEntry,policy:TradeJournalPolicy)->TradeJournalSummary:...
