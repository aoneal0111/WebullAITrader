from app.trade_journal.exceptions import TradeJournalSerializationError
from app.trade_journal.models import *
from app.trade_journal.policies import TradeJournalPolicy
def _s(v,t):
    if not isinstance(v,t):raise TradeJournalSerializationError(f"value must be {t.__name__}")
    return v.to_dict()
serialize_identity=lambda v:_s(v,TradeJournalIdentity)
serialize_entry=lambda v:_s(v,TradeJournalEntry)
serialize_state=lambda v:_s(v,TradeJournalState)
serialize_request=lambda v:_s(v,TradeJournalAppendRequest)
serialize_result=lambda v:_s(v,TradeJournalAppendResult)
serialize_policy=lambda v:_s(v,TradeJournalPolicy)
serialize_criteria=lambda v:_s(v,TradeJournalCriteriaResult)
serialize_summary=lambda v:_s(v,TradeJournalSummary)
