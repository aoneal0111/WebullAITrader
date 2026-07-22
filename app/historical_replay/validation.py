from app.historical_replay.exceptions import HistoricalReplayDependencyError,HistoricalReplayValidationError
from app.historical_replay.models import HistoricalReplayRequest
from app.historical_replay.policies import HistoricalReplayPolicy
def validate_dependencies(coordinator,policy):
    if coordinator is None or not callable(getattr(coordinator,"execute",None)):raise HistoricalReplayDependencyError("coordinator must expose execute(request)")
    if not isinstance(policy,HistoricalReplayPolicy):raise HistoricalReplayDependencyError("policy must be HistoricalReplayPolicy")
def validate_request(request,policy,minimal=False):
    if not isinstance(request,HistoricalReplayRequest):raise HistoricalReplayValidationError("request must be HistoricalReplayRequest")
    if minimal:return request
    events=request.events
    if not events and not policy.allow_empty_events:raise HistoricalReplayValidationError("events cannot be empty")
    if policy.maximum_events is not None and len(events)>policy.maximum_events:raise HistoricalReplayValidationError("maximum_events exceeded")
    if not policy.allow_duplicate_event_ids and len({x.event_id for x in events})!=len(events):raise HistoricalReplayValidationError("duplicate event IDs")
    if not policy.allow_duplicate_sequences and len({x.sequence for x in events})!=len(events):raise HistoricalReplayValidationError("duplicate event sequences")
    if policy.require_unique_orchestrator_request_ids and len({x.orchestrator_request_id for x in events})!=len(events):raise HistoricalReplayValidationError("duplicate orchestrator request IDs")
    if len({x.cycle_provenance.cycle_id for x in events})!=len(events):raise HistoricalReplayValidationError("duplicate cycle IDs")
    for event in events:
        if event.portfolio.account_id!=request.identity.account_id:raise HistoricalReplayValidationError("event portfolio account mismatch")
        if event.cycle_provenance.portfolio_before.account_id!=request.identity.account_id:raise HistoricalReplayValidationError("cycle provenance portfolio account mismatch")
        if event.cycle_provenance.original_account.account_id!=request.identity.account_id:raise HistoricalReplayValidationError("cycle provenance original account mismatch")
        if event.requested_quantity is None or event.requested_quantity<=0:raise HistoricalReplayValidationError("event requested_quantity must be positive")
    return request
