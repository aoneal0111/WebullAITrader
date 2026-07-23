from pathlib import Path
def test_no_runtime_external_or_hidden_state_dependencies_and_no_reverse_import():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/trade_journal").glob("*.py"))
    prohibited=("strategyruntime","riskruntime","executionplannerruntime","papertradingruntime","executionorchestratorruntime","orderplacementruntime","webull","broker_gateway","authentication","app.session","httpx","requests","socket","os.environ","getenv(","datetime.now","utcnow","uuid","random","retry","poll","sleep(","threading","asyncio","open(","pathlib","sqlite","database","registry","cache")
    assert not [x for x in prohibited if x in text]
    reverse="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/trading_cycle").glob("*.py"))
    assert "app.trade_journal" not in reverse
