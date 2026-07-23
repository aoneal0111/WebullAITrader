from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import pytest
from app.analytics import *
from app.trade_journal import TradeJournalState,TradeJournalStatus
from tests.analytics.helpers import Evaluator,entry,journal,request,runtime

def test_enabled_exactly_once_disabled_zero_calls():
    req=request((entry(),));summary=runtime().evaluate(req).summary;e=Evaluator(summary)
    assert AnalyticsRuntime(e,AnalyticsPolicy(enabled=True)).evaluate(req).summary is summary and len(e.calls)==1
    disabled=Evaluator();result=AnalyticsRuntime(disabled,AnalyticsPolicy()).evaluate(req)
    assert result.status is AnalyticsStatus.DISABLED and result.summary is None and result.disabled and disabled.calls==[]

def test_structural_failure_zero_calls():
    e=Evaluator();engine=AnalyticsRuntime(e,AnalyticsPolicy(enabled=True,require_active_journal=True))
    with pytest.raises(AnalyticsValidationError):engine.evaluate(request((entry(),),status=TradeJournalStatus.ARCHIVED))
    assert e.calls==[]

def test_evaluator_exception_normalization_cause():
    e=Evaluator(error=KeyError("raw"))
    with pytest.raises(AnalyticsEvaluationError) as caught:AnalyticsRuntime(e,AnalyticsPolicy(enabled=True)).evaluate(request((entry(),)))
    assert isinstance(caught.value.__cause__,KeyError) and len(e.calls)==1

def test_evaluator_identity_and_count_validation():
    req=request((entry(),));good=runtime().evaluate(req).summary
    with pytest.raises(AnalyticsDependencyError):AnalyticsRuntime(Evaluator(replace(good,request_id="wrong")),AnalyticsPolicy(enabled=True)).evaluate(req)
    with pytest.raises(AnalyticsValidationError):replace(good.metrics,total_entries=2,unclassified_trades=1)

def test_invalid_request_values():
    req=request()
    with pytest.raises(AnalyticsValidationError):replace(req,request_id="")
    with pytest.raises(AnalyticsValidationError):replace(req,as_of=datetime(2026,1,1))
    with pytest.raises(AnalyticsValidationError):replace(req,starting_equity=Decimal("-1"))
    with pytest.raises(AnalyticsValidationError):runtime().evaluate(replace(request((entry(),)),as_of=entry().recorded_at.replace(year=2025)))

@pytest.mark.parametrize("field",["filled_quantity","fees","starting_equity","ending_equity"])
def test_negative_entry_values_rejected_before_evaluator(field):
    bad=replace(entry(),**{field:Decimal("-1")});e=Evaluator()
    with pytest.raises(AnalyticsValidationError):AnalyticsRuntime(e,AnalyticsPolicy(enabled=True)).evaluate(request((bad,)))
    assert e.calls==[]

def test_duplicate_cycle_validation_and_explicit_journal_permission():
    one=entry(0);two=replace(entry(1),cycle_id=one.cycle_id);e=Evaluator()
    with pytest.raises(AnalyticsValidationError):AnalyticsRuntime(e,AnalyticsPolicy(enabled=True)).evaluate(request((one,two)))
    allowed=AnalyticsRequest("a",journal((one,two),metadata={"allow_duplicate_cycle_ids":True}),two.recorded_at,{})
    assert runtime().evaluate(allowed).summary.metrics.total_entries==2

def test_deterministic_no_mutation_no_hidden_state():
    req=request((entry(),));before=req.to_dict();engine=runtime();a=engine.evaluate(req);b=engine.evaluate(req)
    assert a==b and a.to_dict()==b.to_dict() and req.to_dict()==before
