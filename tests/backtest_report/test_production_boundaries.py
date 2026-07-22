from pathlib import Path
def test_no_prohibited_dependencies_or_behavior():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/backtest_report").glob("*.py"))
    prohibited=("historicalreplayruntime","replaycycleprojectionruntime","tradejournalbatchruntime","analyticsruntime","strategyruntime","riskruntime","executionplanner","papertradingruntime","executionorchestrator","tradingcyclebuilder","tradejournalruntime","webull","broker","adapter","live trading","storage","persistence","pathlib","glob","open(","csv","pandas","sqlalchemy","database","exporter","pdf","html","template","matplotlib","plot","httpx","requests","aiohttp","urllib","socket","datetime.now","utcnow","date.today","time.time","uuid","random","secrets","threading","multiprocessing","asyncio","retry","poll")
    assert not [x for x in prohibited if x in text]
def test_no_reverse_dependency():
    roots=("app/backtest_run","app/analytics","app/trade_journal_batch","app/replay_cycle_projection","app/historical_replay","app/trade_journal","app/trading_cycle","app/execution_orchestrator")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert "app.backtest_report" not in text
