from pathlib import Path
def test_no_upstream_runtime_external_or_hidden_state_dependencies():
    files=("domain_models.py","exceptions.py","interfaces.py","policies.py","runtime.py","serializers.py","validation.py")
    text="\n".join((Path("app/analytics")/x).read_text(encoding="utf-8").lower() for x in files)
    prohibited=("strategyruntime","riskruntime","executionplannerruntime","papertradingruntime","executionorchestratorruntime","tradingcyclebuilder","tradejournalruntime","orderplacementruntime","webull","broker_adapter","authentication","app.session","httpx","requests","socket","os.environ","getenv(","datetime.now","utcnow","uuid","random","retry","poll","sleep(","threading","asyncio","open(","pathlib","sqlite","database","registry","cache")
    assert not [x for x in prohibited if x in text]
def test_no_reverse_dependency():
    roots=("app/trade_journal","app/trading_cycle","app/execution_orchestrator","app/paper_trading")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert "app.analytics" not in text
