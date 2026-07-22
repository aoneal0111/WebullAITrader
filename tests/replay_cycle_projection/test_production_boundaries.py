from pathlib import Path
def test_no_prohibited_dependencies_or_behavior():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/replay_cycle_projection").glob("*.py"))
    prohibited=("historicalreplayruntime","executionorchestratorruntime","papertradingruntime","strategyruntime","riskruntime","executionplannerruntime","tradejournalruntime","analyticsruntime","webull","broker","httpx","requests","socket","os.environ","getenv(","pathlib","open(","datetime.now","utcnow","date.today","uuid","random","retry","poll","sleep(","threading","multiprocessing","asyncio","database","registry","cache","profit_loss","drawdown","win_rate")
    assert not [x for x in prohibited if x in text]
def test_no_reverse_dependency():
    roots=("app/historical_replay","app/trading_cycle","app/trade_journal","app/analytics","app/execution_orchestrator","app/paper_trading","app/strategy","app/risk")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert "app.replay_cycle_projection" not in text
