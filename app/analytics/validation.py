from app.analytics.domain_models import AnalyticsRequest
from app.analytics.exceptions import AnalyticsDependencyError,AnalyticsValidationError
from app.analytics.policies import AnalyticsPolicy
from app.trade_journal import TradeJournalStatus
def validate_dependencies(evaluator,policy):
    if evaluator is None or not callable(getattr(evaluator,"evaluate",None)):raise AnalyticsDependencyError("analytics evaluator must expose evaluate(request, policy)")
    if not isinstance(policy,AnalyticsPolicy):raise AnalyticsDependencyError("policy must be AnalyticsPolicy")
def validate_request(request,policy):
    if not isinstance(request,AnalyticsRequest):raise AnalyticsValidationError("request must be AnalyticsRequest")
    journal=request.journal;entries=journal.entries
    if journal.total_entries!=len(entries):raise AnalyticsValidationError("journal total_entries mismatch")
    if policy.require_active_journal and journal.status is not TradeJournalStatus.ACTIVE:raise AnalyticsValidationError("journal must be active")
    if policy.require_entries and not entries:raise AnalyticsValidationError("journal entries are required")
    if len({x.entry_id for x in entries})!=len(entries):raise AnalyticsValidationError("duplicate journal entry IDs")
    if not journal.metadata.get("allow_duplicate_cycle_ids",False) and len({x.cycle_id for x in entries})!=len(entries):raise AnalyticsValidationError("duplicate cycle IDs")
    previous=None
    for entry in entries:
        if entry.journal_id!=journal.journal_id:raise AnalyticsValidationError("journal entry identity mismatch")
        if previous is not None and entry.recorded_at<previous:raise AnalyticsValidationError("journal entries are not chronological")
        previous=entry.recorded_at
        for name in ("requested_quantity","approved_quantity","planned_quantity","filled_quantity","fees","starting_equity","ending_equity"):
            value=getattr(entry,name)
            if value is not None and value<0:raise AnalyticsValidationError(f"entry {name} cannot be negative")
    if entries and request.as_of<entries[-1].recorded_at:raise AnalyticsValidationError("as_of cannot precede journal history")
    return request
