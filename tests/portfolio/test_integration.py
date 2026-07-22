from app.portfolio import DeterministicPortfolioRuntime
from tests.portfolio.fixtures import enabled_policy,request
from tests.portfolio.helpers import FakeAccountRuntime,FakePositionsRuntime
def test_composition_only_integration():
 a,p=FakeAccountRuntime(),FakePositionsRuntime();result=DeterministicPortfolioRuntime(a,p,enabled_policy()).get_portfolio(request());assert result.success and len(a.requests)==len(p.requests)==1
