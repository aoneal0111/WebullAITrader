from app.portfolio import PortfolioPosition,PortfolioSnapshot
from app.strategy import StrategyContext,StrategyPolicy
def portfolio():
 position=PortfolioPosition("aapl","2","250","200","50","1");return PortfolioSnapshot("account-1","500","1000","750","250","750",(position,),{"source":"synthetic"})
def context():return StrategyContext("context-1",portfolio(),{"strategy":"synthetic","threshold":0.5},{"source":"test"})
def enabled_policy():return StrategyPolicy(enabled=True)
