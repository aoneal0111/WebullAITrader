import pytest
from app.strategy import *
from tests.strategy.fixtures import context,enabled_policy
from tests.strategy.helpers import FakeEvaluator
def test_construction_no_work():
 evaluator=FakeEvaluator();DeterministicStrategyRuntime(evaluator,enabled_policy());assert not evaluator.contexts
def test_enabled_evaluates_exactly_once_and_preserves_context():
 evaluator=FakeEvaluator();result=DeterministicStrategyRuntime(evaluator,enabled_policy()).evaluate(context());assert result.evaluated and evaluator.contexts==[context()] and result.decisions==evaluator.response
def test_disabled_never_evaluates():
 evaluator=FakeEvaluator();result=DeterministicStrategyRuntime(evaluator,StrategyPolicy()).evaluate(context());assert not result.evaluated and result.decisions==() and not evaluator.contexts
def test_evaluator_failure_normalized_with_cause_no_retry():
 evaluator=FakeEvaluator(error=KeyError("raw"))
 with pytest.raises(StrategyEvaluationError) as caught:DeterministicStrategyRuntime(evaluator,enabled_policy()).evaluate(context())
 assert isinstance(caught.value.__cause__,KeyError) and len(evaluator.contexts)==1
def test_invalid_evaluator_output_rejected():
 with pytest.raises(StrategyDependencyError):DeterministicStrategyRuntime(FakeEvaluator(response=["bad"]),enabled_policy()).evaluate(context())
def test_equivalent_execution_deterministic_and_input_immutable():
 value=context();before=value.to_dict();assert DeterministicStrategyRuntime(FakeEvaluator(),enabled_policy()).evaluate(value)==DeterministicStrategyRuntime(FakeEvaluator(),enabled_policy()).evaluate(value) and value.to_dict()==before
