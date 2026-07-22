from pathlib import Path
def test_no_prohibited_dependencies_or_behavior():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/historical_replay").glob("*.py"))
    prohibited=("strategyruntime","riskruntime","executionplannerruntime","papertradingruntime","tradingcyclebuilder","tradejournalruntime","analyticsruntime","orderplacementruntime","webull","broker_adapter","authentication","app.session","httpx","requests","socket","os.environ","getenv(","datetime.now","utcnow","date.today","uuid","random","retry","poll","sleep(","threading","multiprocessing","asyncio","open(","pathlib","sqlite","database","registry","cache","profit_loss","drawdown","win_rate")
    assert not [x for x in prohibited if x in text]
def test_no_reverse_dependency():
    roots=("app/execution_orchestrator","app/trading_cycle","app/trade_journal","app/analytics","app/strategy","app/risk","app/paper_trading")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert "app.historical_replay" not in text
