from inspect import signature
from app.portfolio import DeterministicPortfolioRuntime,PortfolioRuntime
def test_exact_interface():assert {n for n in dir(DeterministicPortfolioRuntime) if not n.startswith("_")}=={"get_portfolio"} and list(signature(PortfolioRuntime.get_portfolio).parameters)==["self","request"]
