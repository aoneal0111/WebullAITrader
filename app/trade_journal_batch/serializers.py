from app.trade_journal_batch.exceptions import TradeJournalBatchSerializationError
from app.trade_journal_batch.models import *
def _s(v,t):
    if not isinstance(v,t):raise TradeJournalBatchSerializationError(f"value must be {t.__name__}")
    return v.to_dict()
serialize_identity=lambda v:_s(v,TradeJournalBatchIdentity)
serialize_item=lambda v:_s(v,TradeJournalBatchItem)
serialize_policy=lambda v:_s(v,TradeJournalBatchPolicy)
serialize_request=lambda v:_s(v,TradeJournalBatchRequest)
serialize_criteria=lambda v:_s(v,TradeJournalBatchCriteriaResult)
serialize_item_result=lambda v:_s(v,TradeJournalBatchItemResult)
serialize_progress=lambda v:_s(v,TradeJournalBatchProgress)
serialize_result=lambda v:_s(v,TradeJournalBatchResult)
