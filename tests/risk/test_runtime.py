import pytest
from app.risk import *
from app.strategy import StrategySignal
from tests.risk.fixtures import context,enabled_policy
from tests.risk.helpers import FakeEvaluator
def runtime(evaluator=None,policy=None):return DeterministicRiskRuntime(evaluator or FakeEvaluator(),policy or enabled_policy())
def test_construction_no_work():
 e=FakeEvaluator();runtime(e);assert not e.calls
def test_approved_invokes_evaluator_once():
 e=FakeEvaluator();result=runtime(e).evaluate(context());assert result.outcome is RiskOutcome.APPROVED and result.approved_quantity==2 and len(e.calls)==1
def test_modified_reduces_quantity_deterministically():
 result=runtime(policy=enabled_policy(max_order_quantity="1")).evaluate(context());assert result.outcome is RiskOutcome.MODIFIED and result.approved_quantity==1
def test_rejected_when_no_safe_capacity_or_modification_disabled():
 assert runtime(policy=enabled_policy(minimum_cash_reserve="500")).evaluate(context()).outcome is RiskOutcome.REJECTED
 assert runtime(policy=enabled_policy(max_order_quantity="1",allow_modification=False)).evaluate(context()).outcome is RiskOutcome.REJECTED
def test_hold_approved_without_quantity_and_exit_limited_to_position():
 assert runtime().evaluate(context(StrategySignal.HOLD,"0")).outcome is RiskOutcome.APPROVED
 result=runtime().evaluate(context(StrategySignal.EXIT,"5"));assert result.outcome is RiskOutcome.MODIFIED and result.approved_quantity==2
def test_disabled_zero_invocations():
 e=FakeEvaluator();result=runtime(e,RiskPolicy()).evaluate(context());assert result.outcome is RiskOutcome.REJECTED and not e.calls
def test_evaluator_failure_normalized_no_retry():
 e=FakeEvaluator(error=KeyError("raw"))
 with pytest.raises(RiskRuntimeEvaluationError) as caught:runtime(e).evaluate(context())
 assert isinstance(caught.value.__cause__,KeyError) and len(e.calls)==1
def test_invalid_evaluator_identity_rejected():
 other=DeterministicRiskEvaluator().evaluate(context(),enabled_policy());object.__setattr__(other,"context_id","other")
 with pytest.raises(RiskRuntimeDependencyError):runtime(FakeEvaluator(other)).evaluate(context())
def test_repeatability_and_input_immutability():
 value=context();before=value.to_dict();assert runtime().evaluate(value)==runtime().evaluate(value) and value.to_dict()==before
