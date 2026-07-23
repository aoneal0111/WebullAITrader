from app.trade_journal.exceptions import TradeJournalDependencyError,TradeJournalValidationError
from app.trade_journal.models import TradeJournalAppendRequest
from app.trade_journal.policies import TradeJournalPolicy
def validate_dependencies(policy,evaluator):
    if not isinstance(policy,TradeJournalPolicy):raise TradeJournalDependencyError("policy must be TradeJournalPolicy")
    if evaluator is not None and not callable(getattr(evaluator,"evaluate",None)):raise TradeJournalDependencyError("summary evaluator must expose evaluate(state, entry, policy)")
def validate_request(request):
    if not isinstance(request,TradeJournalAppendRequest):raise TradeJournalValidationError("request must be TradeJournalAppendRequest")
    return request
