from app.portfolio import PortfolioPosition,PortfolioSnapshot
from app.risk import RiskContext,RiskPolicy
from app.strategy import StrategyContext,StrategyDecision,StrategySignal
def strategy_context(cash="500"):
 p=PortfolioPosition("AAPL","2","250","200","50","1");portfolio=PortfolioSnapshot("account-1",cash,"1000","750","250",str(int(cash)+250),(p,));return StrategyContext("strategy-context",portfolio,{"strategy":"synthetic"})
def context(signal=StrategySignal.BUY,quantity="2",price="100",cash="500"):
 sc=strategy_context(cash);return RiskContext("risk-context",sc,StrategyDecision("AAPL",signal,"0.8",("synthetic",)),quantity,price,{"source":"synthetic"})
def enabled_policy(**changes):
 values={"enabled":True};values.update(changes);return RiskPolicy(**values)
