from pathlib import Path
def test_no_prohibited_dependencies_or_behavior():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/backtest_run").glob("*.py"))
    prohibited=("strategyruntime","riskruntime","executionplannerruntime","papertradingruntime","executionorchestrator","tradingcyclebuilder","tradejournalruntime","webull","broker","live trading","storage","persistence","pathlib","glob","csv","pandas","sqlalchemy","database","reporting","evidence","httpx","requests","aiohttp","urllib","socket","datetime.now","utcnow","date.today","time.time","uuid","random","secrets","threading","multiprocessing","asyncio","retry","poll","open(")
    assert not [x for x in prohibited if x in text]
def test_no_reverse_dependency():
    roots=("app/historical_replay","app/replay_cycle_projection","app/trade_journal_batch","app/analytics","app/trade_journal","app/trading_cycle","app/execution_orchestrator")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert "app.backtest_run" not in text
