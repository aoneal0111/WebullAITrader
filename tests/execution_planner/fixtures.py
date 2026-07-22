from app.execution_planner import ExecutionPlanRequest,ExecutionPlannerPolicy
from app.portfolio import PortfolioPosition,PortfolioSnapshot
from app.risk import RiskContext,RiskCriteriaResult,RiskOutcome,RiskResult
from app.strategy import StrategyContext,StrategyDecision,StrategySignal
def request(signal=StrategySignal.BUY,outcome=RiskOutcome.APPROVED,requested="2",approved="2",configuration=None):
 p=PortfolioPosition("AAPL","2","250","200","50","1");portfolio=PortfolioSnapshot("account-1","500","1000","750","250","750",(p,));sc=StrategyContext("strategy-context",portfolio,configuration or {"order_type":"LIMIT","time_in_force":"DAY"});sd=StrategyDecision("AAPL",signal,"0.8",("synthetic",));rc=RiskContext("risk-context",sc,sd,requested,"100");rr=RiskResult("risk-context",sd,outcome,requested,approved,(RiskCriteriaResult("risk",outcome is not RiskOutcome.REJECTED,requested,None,"synthetic risk"),),"risk-v1");return ExecutionPlanRequest("plan-request",rc,rr,{"source":"synthetic"})
def enabled_policy():return ExecutionPlannerPolicy(enabled=True)
