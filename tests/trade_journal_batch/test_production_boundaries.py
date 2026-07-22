from pathlib import Path
def test_no_prohibited_dependencies_or_behavior():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/trade_journal_batch").glob("*.py"))
    prohibited=("historicalreplayruntime","replaycycleprojectionruntime","tradingcyclebuilder","executionorchestrator","strategyruntime","riskruntime","executionplannerruntime","papertradingruntime","analyticsruntime","backtestrunruntime","webull","broker","storage","persistence","reporting","evidence","httpx","requests","aiohttp","urllib","socket","pathlib","glob","csv","pandas","sqlalchemy","database","datetime.now","utcnow","date.today","time.time","uuid","random","secrets","threading","multiprocessing","asyncio","retry","poll","open(")
    assert not [x for x in prohibited if x in text]
def test_no_reverse_dependency():
    roots=("app/trade_journal","app/trading_cycle","app/replay_cycle_projection","app/historical_replay","app/analytics","app/execution_orchestrator")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert "app.trade_journal_batch" not in text
