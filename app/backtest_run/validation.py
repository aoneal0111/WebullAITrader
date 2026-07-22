from app.backtest_run.exceptions import BacktestRunDependencyError,BacktestRunValidationError
from app.backtest_run.models import BacktestRunRequest
def validate_dependencies(replay,projection,journal,analytics,pfactory,jfactory,afactory):
    expected=((replay,"replay"),(projection,"project"),(journal,"run"),(analytics,"evaluate"),(pfactory,"create"),(jfactory,"create"),(afactory,"create"))
    if any(obj is None or not callable(getattr(obj,method,None)) for obj,method in expected):raise BacktestRunDependencyError("all stage runtimes and factories are required")
def validate_request(request,minimal=False):
    if not isinstance(request,BacktestRunRequest):raise BacktestRunValidationError("request must be BacktestRunRequest")
    if minimal:return request
    if request.replay_request.identity.run_id is not None and request.replay_request.identity.run_id!=request.identity.run_id:raise BacktestRunValidationError("replay run identity mismatch")
    if request.journal_input.identity.source_run_id is not None and request.journal_input.identity.source_run_id!=request.identity.run_id:raise BacktestRunValidationError("journal run identity mismatch")
    if request.journal_input.identity.journal_id!=request.journal_input.initial_journal.journal_id:raise BacktestRunValidationError("journal identity mismatch")
    cycle_ids=tuple(x.cycle_id for x in request.journal_input.items);entry_ids=tuple(x.entry_id for x in request.journal_input.items)
    if len(set(cycle_ids))!=len(cycle_ids):raise BacktestRunValidationError("duplicate journal cycle input")
    if len(set(entry_ids))!=len(entry_ids):raise BacktestRunValidationError("duplicate journal entry input")
    return request
