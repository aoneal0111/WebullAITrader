from app.portfolio import PortfolioSnapshot
from app.strategy import DeterministicStrategyRuntime,StrategySignal
from tests.strategy.fixtures import context,enabled_policy
from tests.strategy.helpers import FakeEvaluator
def test_portfolio_snapshot_to_broker_neutral_advisory_result():
 value=context();assert isinstance(value.portfolio,PortfolioSnapshot);result=DeterministicStrategyRuntime(FakeEvaluator(),enabled_policy()).evaluate(value);assert result.decisions[0].signal is StrategySignal.HOLD
