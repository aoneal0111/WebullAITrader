from decimal import Decimal
from app.analytics.domain_models import *
from app.analytics.exceptions import AnalyticsDependencyError,AnalyticsEvaluationError
from app.analytics.validation import validate_dependencies,validate_request
from app.trade_journal import TradeJournalEntryType

class DeterministicAnalyticsEvaluator:
    """Read-only exact-Decimal analytics over journal entry order."""
    def evaluate(self,request,policy):
        entries=request.journal.entries
        classifications=[];classified_values=[];wins=[];losses=[]
        for entry in entries:
            value=entry.realized_profit_loss
            if value is None:classification=AnalyticsEntryClassification.UNCLASSIFIED
            elif value>0:classification=AnalyticsEntryClassification.WIN;wins.append(value);classified_values.append(value)
            elif value<0:classification=AnalyticsEntryClassification.LOSS;losses.append(value);classified_values.append(value)
            elif policy.classify_zero_realized_profit_loss_as_breakeven:classification=AnalyticsEntryClassification.BREAKEVEN;classified_values.append(value)
            else:classification=AnalyticsEntryClassification.UNCLASSIFIED
            classifications.append(classification)
        classified=len(classified_values);winning=len(wins);losing=len(losses);breakeven=sum(x is AnalyticsEntryClassification.BREAKEVEN for x in classifications);unclassified=len(entries)-classified
        D=Decimal;gross_profit=sum(wins,D("0")) if classified else None;gross_loss=sum(losses,D("0")) if classified else None
        net=gross_profit+gross_loss if classified else None;average=net/D(classified) if classified else None
        rates=tuple(D(count)/D(classified) if classified else None for count in (winning,losing,breakeven))
        fees=[x.fees for x in entries if x.fees is not None];quantities=[x.filled_quantity for x in entries if x.filled_quantity is not None]
        equity_curve=[];running=None
        for entry in entries:
            if entry.realized_profit_loss is not None:running=(running or D("0"))+entry.realized_profit_loss
            if entry.ending_equity is not None:
                equity_curve.append(EquityPoint(len(equity_curve),entry.entry_id,entry.cycle_id,entry.recorded_at,entry.ending_equity,entry.equity_change,running))
        drawdowns=[];peak=None
        for point in equity_curve:
            peak=point.equity if peak is None or point.equity>peak else peak;amount=peak-point.equity
            drawdowns.append(DrawdownPoint(len(drawdowns),point.entry_id,point.cycle_id,point.recorded_at,point.equity,peak,amount,amount/peak if peak>0 else None,DrawdownStatus.AT_PEAK if amount==0 else DrawdownStatus.IN_DRAWDOWN))
        starting=request.starting_equity
        if starting is None:starting=next((x.starting_equity for x in entries if x.starting_equity is not None),None)
        ending=next((x.ending_equity for x in reversed(entries) if x.ending_equity is not None),None)
        category={kind:sum(x.entry_type is kind for x in entries) for kind in TradeJournalEntryType}
        metrics=AnalyticsMetrics(len(entries),category[TradeJournalEntryType.EXECUTION],category[TradeJournalEntryType.PARTIAL_EXECUTION],category[TradeJournalEntryType.NO_ACTION],category[TradeJournalEntryType.REJECTION],category[TradeJournalEntryType.FAILURE],category[TradeJournalEntryType.DISABLED],classified,winning,losing,breakeven,unclassified,*rates,gross_profit,gross_loss,net,average,gross_profit/D(winning) if winning else None,gross_loss/D(losing) if losing else None,max(wins) if wins else None,min(losses) if losses else None,gross_profit/abs(gross_loss) if gross_loss is not None and gross_loss<0 else None,average,sum(fees,D("0")) if fees else None,sum(quantities,D("0")) if quantities else None,sum(quantities,D("0"))/D(len(quantities)) if quantities else None,starting,ending,max((x.equity for x in equity_curve),default=None),min((x.equity for x in equity_curve),default=None),max((x.drawdown_amount for x in drawdowns),default=None),max((x.drawdown_percentage for x in drawdowns if x.drawdown_percentage is not None),default=None),drawdowns[-1].drawdown_amount if drawdowns else None,drawdowns[-1].drawdown_percentage if drawdowns else None,ending-starting if ending is not None and starting is not None else None,entries[0].recorded_at if entries else None,entries[-1].recorded_at if entries else None)
        usable=any(x.realized_profit_loss is not None or x.ending_equity is not None or x.fees is not None or x.filled_quantity is not None for x in entries)
        insufficient=not entries or not usable or (policy.minimum_classified_trades is not None and classified<policy.minimum_classified_trades)
        warnings=[]
        if not entries:warnings.append("journal contains no entries")
        elif not usable:warnings.append("journal contains no usable analytics values")
        if policy.minimum_classified_trades is not None and classified<policy.minimum_classified_trades:warnings.append("minimum classified trades not met")
        status=AnalyticsStatus.INSUFFICIENT_DATA if insufficient else AnalyticsStatus.COMPLETED
        diagnostics=(f"classified={classified}",f"equity_points={len(equity_curve)}") if policy.include_diagnostics else ()
        return AnalyticsSummary(request.request_id,request.journal.journal_id,status,metrics,tuple(equity_curve) if policy.include_equity_curve else (),tuple(drawdowns) if policy.include_drawdown_curve else (),tuple(warnings),diagnostics,{"deterministic":True})

class AnalyticsRuntime:
    def __init__(self,evaluator,policy):validate_dependencies(evaluator,policy);self._evaluator=evaluator;self._policy=policy
    def evaluate(self,request):
        request=validate_request(request,self._policy)
        if not self._policy.enabled:return AnalyticsResult(request.request_id,request.journal.journal_id,AnalyticsStatus.DISABLED,None,(AnalyticsCriteriaResult("policy_enabled",False,("analytics disabled",)),),(),(),{"deterministic":True},True)
        try:summary=self._evaluator.evaluate(request,self._policy)
        except Exception as exc:raise AnalyticsEvaluationError("analytics evaluator failed") from exc
        self._validate_output(request,summary)
        criteria=(AnalyticsCriteriaResult("policy_enabled",True,()),AnalyticsCriteriaResult("identity_continuity",True,()),AnalyticsCriteriaResult("metrics_consistent",True,()))
        return AnalyticsResult(request.request_id,request.journal.journal_id,summary.status,summary,criteria,summary.warnings,(),{"deterministic":True,"policy_version":self._policy.version},False)
    @staticmethod
    def _validate_output(request,summary):
        if not isinstance(summary,AnalyticsSummary):raise AnalyticsDependencyError("analytics evaluator returned invalid summary")
        if summary.request_id!=request.request_id or summary.journal_id!=request.journal.journal_id:raise AnalyticsDependencyError("analytics summary identity mismatch")
        if summary.metrics.total_entries!=request.journal.total_entries:raise AnalyticsDependencyError("analytics metric entry count mismatch")
        entry_order={x.entry_id:i for i,x in enumerate(request.journal.entries)}
        for curve in (summary.equity_curve,summary.drawdown_curve):
            if tuple(x.sequence for x in curve)!=tuple(range(len(curve))) or any(x.entry_id not in entry_order for x in curve) or tuple(entry_order[x.entry_id] for x in curve)!=tuple(sorted(entry_order[x.entry_id] for x in curve)):raise AnalyticsDependencyError("analytics curve ordering invalid")
        if summary.drawdown_curve and tuple(x.entry_id for x in summary.drawdown_curve)!=tuple(x.entry_id for x in summary.equity_curve):raise AnalyticsDependencyError("drawdown and equity curves mismatch")
