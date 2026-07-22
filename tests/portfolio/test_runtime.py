from decimal import Decimal
import pytest
from app.account_information import AccountInformationDecision
from app.portfolio import *
from app.positions import PositionsDecision
from tests.portfolio.fixtures import enabled_policy,request
from tests.portfolio.helpers import *
def runtime(accounts=None,positions_runtime=None,policy=None):return DeterministicPortfolioRuntime(accounts or FakeAccountRuntime(),positions_runtime or FakePositionsRuntime(),policy or enabled_policy())
def test_construction_no_work():
 a,p=FakeAccountRuntime(),FakePositionsRuntime();runtime(a,p);assert not a.requests and not p.requests
def test_success_calls_each_dependency_once_and_calculates_totals():
 a,p=FakeAccountRuntime(),FakePositionsRuntime();result=runtime(a,p).get_portfolio(request());assert result.success and a.requests==[request()] and p.requests==[request()]
 assert result.market_value==Decimal("400") and result.total_value==Decimal("900") and result.cash==Decimal("500")
 assert result.positions[0].cost_basis==Decimal("200") and result.positions[0].weight==Decimal("0.625")
def test_disabled_calls_nothing():
 a,p=FakeAccountRuntime(),FakePositionsRuntime();result=runtime(a,p,PortfolioPolicy()).get_portfolio(request());assert result.decision is PortfolioDecision.DISABLED and not a.requests and not p.requests
@pytest.mark.parametrize("account_response,positions_response",[(account(decision=AccountInformationDecision.GATEWAY_FAILURE),positions()),(account(),positions(decision=PositionsDecision.GATEWAY_FAILURE)),("bad",positions()),(account(),"bad")])
def test_dependency_failure_results(account_response,positions_response):assert runtime(FakeAccountRuntime(account_response),FakePositionsRuntime(positions_response)).get_portfolio(request()).decision is PortfolioDecision.DEPENDENCY_FAILURE
def test_dependency_exceptions_do_not_retry():
 a,p=FakeAccountRuntime(error=OSError("synthetic")),FakePositionsRuntime();result=runtime(a,p).get_portfolio(request());assert result.decision is PortfolioDecision.DEPENDENCY_FAILURE and len(a.requests)==1 and not p.requests
 a,p=FakeAccountRuntime(),FakePositionsRuntime(error=OSError("synthetic"));result=runtime(a,p).get_portfolio(request());assert result.decision is PortfolioDecision.DEPENDENCY_FAILURE and len(a.requests)==len(p.requests)==1
@pytest.mark.parametrize("a,p",[(account("other"),positions()),(account(),positions((position("other"),)))])
def test_invalid_account(a,p):assert runtime(FakeAccountRuntime(a),FakePositionsRuntime(p)).get_portfolio(request()).decision is PortfolioDecision.INVALID_ACCOUNT
def test_empty_positions_and_zero_weight():
 result=runtime(positions_runtime=FakePositionsRuntime(positions(()))).get_portfolio(request());assert result.success and result.market_value==0 and result.total_value==result.cash and result.positions==()
def test_deterministic_and_input_immutable():
 value=request();before=value.to_dict();assert runtime().get_portfolio(value)==runtime().get_portfolio(value) and value.to_dict()==before
