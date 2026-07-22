from pathlib import Path
from app.trading_cycle import DefaultTradingCycleMetricsEvaluator,TradingCycleMetricsEvaluator

def test_default_evaluator_interface():assert callable(DefaultTradingCycleMetricsEvaluator().evaluate)

def test_no_runtime_or_external_dependencies_and_reverse_imports():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/trading_cycle").glob("*.py"))
    prohibited=("strategyruntime","riskruntime","executionplannerruntime","papertradingruntime","orderplacementruntime","webull","broker_gateway","authentication","app.session","httpx","requests","socket","os.environ","getenv(","datetime.now","utcnow","uuid","random","retry","poll","sleep(","threading","asyncio","open(","pathlib","sqlite","database","registry","cache")
    assert not [x for x in prohibited if x in text]
    existing=("app/strategy","app/risk","app/execution_planner","app/paper_trading","app/execution_orchestrator")
    reverse="\n".join(p.read_text(encoding="utf-8").lower() for root in existing for p in Path(root).glob("*.py"))
    assert "app.trading_cycle" not in reverse
