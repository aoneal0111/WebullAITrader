from app.execution_orchestrator import PaperTradingCycleOutcome
from app.historical_replay import HistoricalReplayEventStatus,HistoricalReplayResult,HistoricalReplayStatus
from app.replay_cycle_projection.exceptions import ReplayCycleProjectionDependencyError,ReplayCycleProjectionValidationError
from app.replay_cycle_projection.models import ReplayCycleProjectionRequest
from app.replay_cycle_projection.policies import ReplayCycleProjectionPolicy
SUCCESS=(PaperTradingCycleOutcome.EXECUTED,PaperTradingCycleOutcome.PARTIALLY_EXECUTED,PaperTradingCycleOutcome.NO_ACTION)
REJECTIONS=(PaperTradingCycleOutcome.STRATEGY_REJECTED,PaperTradingCycleOutcome.RISK_REJECTED,PaperTradingCycleOutcome.PLANNING_REJECTED,PaperTradingCycleOutcome.EXECUTION_REJECTED)
def validate_dependencies(builder,policy):
    if builder is None or not callable(getattr(builder,"build",None)):raise ReplayCycleProjectionDependencyError("builder must expose build(request)")
    if not isinstance(policy,ReplayCycleProjectionPolicy):raise ReplayCycleProjectionDependencyError("policy must be ReplayCycleProjectionPolicy")
def validate_request(request,minimal=False):
    if not isinstance(request,ReplayCycleProjectionRequest):raise ReplayCycleProjectionValidationError("request must be ReplayCycleProjectionRequest")
    if minimal:return request
    replay=request.replay_result
    if replay.status is HistoricalReplayStatus.DISABLED:raise ReplayCycleProjectionValidationError("disabled replay result cannot be projected")
    for item in replay.event_results:
        p=item.cycle_provenance
        if p.portfolio_before.account_id!=replay.identity.account_id or p.original_account.account_id!=replay.identity.account_id:raise ReplayCycleProjectionValidationError("cycle provenance account mismatch")
        if item.orchestrator_result is not None:
            o=item.orchestrator_result
            if item.orchestrator_request_id!=o.request_id or o.account_id!=replay.identity.account_id:raise ReplayCycleProjectionValidationError("orchestrator identity mismatch")
            if item.resulting_state!=o.resulting_account:raise ReplayCycleProjectionValidationError("resulting state mismatch")
            if item.status is HistoricalReplayEventStatus.COMPLETED and o.outcome not in SUCCESS:raise ReplayCycleProjectionValidationError("completed replay event outcome mismatch")
            if item.status is HistoricalReplayEventStatus.REJECTED and o.outcome not in REJECTIONS+(PaperTradingCycleOutcome.DISABLED,):raise ReplayCycleProjectionValidationError("rejected replay event outcome mismatch")
    return request
def eligibility(item):
    if item.orchestrator_result is None:return None
    if item.status is HistoricalReplayEventStatus.COMPLETED and item.orchestrator_result.outcome in SUCCESS:return "completed"
    if item.status is HistoricalReplayEventStatus.REJECTED and item.orchestrator_result.outcome in REJECTIONS:return "rejected"
    return None
