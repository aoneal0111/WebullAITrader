from app.analytics import AnalyticsRequest,AnalyticsResult,AnalyticsStatus
from app.historical_replay import HistoricalReplayResult,HistoricalReplayStatus
from app.replay_cycle_projection import ReplayCycleProjectionRequest,ReplayCycleProjectionResult,ReplayCycleProjectionStatus
from app.trade_journal_batch import TradeJournalBatchItem,TradeJournalBatchRequest,TradeJournalBatchResult,TradeJournalBatchStatus
from app.backtest_run.exceptions import BacktestRunResultError,BacktestRunValidationError
from app.backtest_run.models import *
from app.backtest_run.validation import validate_dependencies,validate_request
class DeterministicProjectionRequestFactory:
    def create(self,request,replay_result):return ReplayCycleProjectionRequest(replay_result,request.projection_metadata)
class DeterministicJournalBatchRequestFactory:
    def create(self,request,projection_result):
        configs={x.cycle_id:x for x in request.journal_input.items};items=[]
        for cycle in projection_result.cycles:
            if cycle.identity.cycle_id not in configs:raise BacktestRunResultError("missing journal item input")
            c=configs[cycle.identity.cycle_id];items.append(TradeJournalBatchItem(c.entry_id,cycle,c.recorded_at,c.metadata))
        j=request.journal_input
        return TradeJournalBatchRequest(j.identity,j.initial_journal,tuple(items),j.journal_policy,j.batch_policy,j.requested_at,j.completed_at,j.metadata)
class DeterministicAnalyticsRequestFactory:
    def create(self,request,journal_result):
        a=request.analytics_input;return AnalyticsRequest(a.request_id,journal_result.final_journal,a.as_of,a.metadata,a.starting_equity)
class BacktestRunRuntime:
    def __init__(self,replay,projection,journal,analytics,projection_factory,journal_factory,analytics_factory):validate_dependencies(replay,projection,journal,analytics,projection_factory,journal_factory,analytics_factory);self._replay=replay;self._projection=projection;self._journal=journal;self._analytics=analytics;self._pf=projection_factory;self._jf=journal_factory;self._af=analytics_factory
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._finish(request,BacktestRunStatus.DISABLED,BacktestRunStage.VALIDATION,None,None,None,None,{},None)
        try:validate_request(request)
        except BacktestRunValidationError:return self._finish(request,BacktestRunStatus.REJECTED,BacktestRunStage.VALIDATION,None,None,None,None,{BacktestRunStage.VALIDATION:(BacktestRunStageStatus.REJECTED,"Backtest request validation rejected.",None)},None)
        stages={BacktestRunStage.VALIDATION:(BacktestRunStageStatus.COMPLETED,None,None)};partial=False
        try:replay=self._replay.replay(request.replay_request)
        except Exception as exc:return self._failed(request,BacktestRunStage.HISTORICAL_REPLAY,None,None,None,stages,exc)
        if not isinstance(replay,HistoricalReplayResult):return self._failed(request,BacktestRunStage.HISTORICAL_REPLAY,None,None,None,stages,BacktestRunResultError())
        if replay.status is HistoricalReplayStatus.EMPTY:
            status=BacktestRunStatus.EMPTY if request.policy.allow_empty else BacktestRunStatus.REJECTED;stages[BacktestRunStage.HISTORICAL_REPLAY]=(BacktestRunStageStatus.COMPLETED if request.policy.allow_empty else BacktestRunStageStatus.REJECTED,"Historical replay was empty.",None);return self._finish(request,status,BacktestRunStage.HISTORICAL_REPLAY,replay,None,None,None,stages,None)
        if replay.status in (HistoricalReplayStatus.FAILED,HistoricalReplayStatus.DISABLED):stages[BacktestRunStage.HISTORICAL_REPLAY]=(BacktestRunStageStatus.FAILED if replay.status is HistoricalReplayStatus.FAILED else BacktestRunStageStatus.REJECTED,"Historical replay did not complete.",None);return self._finish(request,BacktestRunStatus.FAILED if replay.status is HistoricalReplayStatus.FAILED else BacktestRunStatus.REJECTED,BacktestRunStage.HISTORICAL_REPLAY,replay,None,None,None,stages,None)
        partial=replay.status is HistoricalReplayStatus.PARTIALLY_COMPLETED;stages[BacktestRunStage.HISTORICAL_REPLAY]=(BacktestRunStageStatus.COMPLETED,None,None)
        try:preq=self._pf.create(request,replay)
        except Exception as exc:return self._failed(request,BacktestRunStage.CYCLE_PROJECTION,replay,None,None,stages,exc)
        if not isinstance(preq,ReplayCycleProjectionRequest):return self._failed(request,BacktestRunStage.CYCLE_PROJECTION,replay,None,None,stages,BacktestRunResultError())
        try:projection=self._projection.project(preq)
        except Exception as exc:return self._failed(request,BacktestRunStage.CYCLE_PROJECTION,replay,None,None,stages,exc)
        if not isinstance(projection,ReplayCycleProjectionResult) or projection.replay_result is not replay:return self._failed(request,BacktestRunStage.CYCLE_PROJECTION,replay,None,None,stages,BacktestRunResultError())
        if projection.status not in (ReplayCycleProjectionStatus.COMPLETED,ReplayCycleProjectionStatus.PARTIALLY_COMPLETED):stages[BacktestRunStage.CYCLE_PROJECTION]=(BacktestRunStageStatus.REJECTED if projection.status in (ReplayCycleProjectionStatus.REJECTED,ReplayCycleProjectionStatus.DISABLED,ReplayCycleProjectionStatus.EMPTY) else BacktestRunStageStatus.FAILED,"Cycle projection did not complete.",None);return self._finish(request,BacktestRunStatus.REJECTED if stages[BacktestRunStage.CYCLE_PROJECTION][0] is BacktestRunStageStatus.REJECTED else BacktestRunStatus.PARTIALLY_COMPLETED,BacktestRunStage.CYCLE_PROJECTION,replay,projection,None,None,stages,None)
        partial=partial or projection.status is ReplayCycleProjectionStatus.PARTIALLY_COMPLETED;stages[BacktestRunStage.CYCLE_PROJECTION]=(BacktestRunStageStatus.COMPLETED,None,None)
        try:jreq=self._jf.create(request,projection)
        except Exception as exc:return self._failed(request,BacktestRunStage.TRADE_JOURNAL_BATCH,replay,projection,None,stages,exc)
        if not isinstance(jreq,TradeJournalBatchRequest):return self._failed(request,BacktestRunStage.TRADE_JOURNAL_BATCH,replay,projection,None,stages,BacktestRunResultError())
        try:journal=self._journal.run(jreq)
        except Exception as exc:return self._failed(request,BacktestRunStage.TRADE_JOURNAL_BATCH,replay,projection,None,stages,exc)
        if not isinstance(journal,TradeJournalBatchResult):return self._failed(request,BacktestRunStage.TRADE_JOURNAL_BATCH,replay,projection,None,stages,BacktestRunResultError())
        if journal.status is not TradeJournalBatchStatus.COMPLETED:stages[BacktestRunStage.TRADE_JOURNAL_BATCH]=(BacktestRunStageStatus.REJECTED if journal.status in (TradeJournalBatchStatus.REJECTED,TradeJournalBatchStatus.DISABLED,TradeJournalBatchStatus.EMPTY) else BacktestRunStageStatus.FAILED,"Trade journal batch did not complete.",None);return self._finish(request,BacktestRunStatus.PARTIALLY_COMPLETED if replay or projection else BacktestRunStatus.REJECTED,BacktestRunStage.TRADE_JOURNAL_BATCH,replay,projection,journal,None,stages,None)
        stages[BacktestRunStage.TRADE_JOURNAL_BATCH]=(BacktestRunStageStatus.COMPLETED,None,None)
        try:areq=self._af.create(request,journal)
        except Exception as exc:return self._failed(request,BacktestRunStage.ANALYTICS,replay,projection,journal,stages,exc)
        if not isinstance(areq,AnalyticsRequest) or areq.journal is not journal.final_journal:return self._failed(request,BacktestRunStage.ANALYTICS,replay,projection,journal,stages,BacktestRunResultError())
        try:analytics=self._analytics.evaluate(areq)
        except Exception as exc:return self._failed(request,BacktestRunStage.ANALYTICS,replay,projection,journal,stages,exc)
        if not isinstance(analytics,AnalyticsResult) or analytics.request_id!=areq.request_id or analytics.journal_id!=areq.journal.journal_id:return self._failed(request,BacktestRunStage.ANALYTICS,replay,projection,journal,stages,BacktestRunResultError())
        if analytics.status is AnalyticsStatus.DISABLED:stages[BacktestRunStage.ANALYTICS]=(BacktestRunStageStatus.REJECTED,"Analytics was disabled.",None);return self._finish(request,BacktestRunStatus.PARTIALLY_COMPLETED,BacktestRunStage.ANALYTICS,replay,projection,journal,analytics,stages,None)
        stages[BacktestRunStage.ANALYTICS]=(BacktestRunStageStatus.COMPLETED,None,None);stages[BacktestRunStage.COMPLETED]=(BacktestRunStageStatus.COMPLETED,None,None)
        return self._finish(request,BacktestRunStatus.PARTIALLY_COMPLETED if partial else BacktestRunStatus.COMPLETED,BacktestRunStage.COMPLETED,replay,projection,journal,analytics,stages,None)
    def _failed(self,request,stage,replay,projection,journal,stages,exc):
        stages[stage]=(BacktestRunStageStatus.FAILED,f"{stage.value.replace('_',' ').title()} stage failed.",type(exc).__name__);prior=bool(replay or projection or journal);return self._finish(request,BacktestRunStatus.PARTIALLY_COMPLETED if prior else BacktestRunStatus.FAILED,stage,replay,projection,journal,None,stages,type(exc).__name__)
    @staticmethod
    def _finish(request,status,stopped,replay,projection,journal,analytics,known,error_type):
        records=[]
        for stage in BacktestRunStage:
            value=known.get(stage)
            records.append(BacktestRunStageResult(stage,*(value or (BacktestRunStageStatus.SKIPPED,None,None))))
        criteria=(BacktestRunCriteriaResult("deterministic_coordination",status not in (BacktestRunStatus.REJECTED,BacktestRunStatus.FAILED),()),)
        return BacktestRunResult(request.identity,status,stopped,replay,projection,journal,analytics,tuple(records),request.requested_at,request.completed_at,criteria,(),(),error_type)
