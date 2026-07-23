from pathlib import Path
import re
def test_only_research_program_boundary_is_used():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/research_portfolio").glob("*.py"))
    prohibited=("app.research_study","app.research_campaign","app.experiment","app.parameter_sweep","app.backtest_suite","app.backtest_run","app.backtest_report","app.historical_replay","app.replay_cycle_projection","app.trade_journal_batch","app.analytics","app.strategy","app.risk","app.execution","app.paper_trading","app.execution_orchestrator","app.trading_cycle","app.broker","webull","app.persistence","app.storage","app.evidence","optimizer","optimization","ranking","recommendation","statistics","exporter","chart","pathlib","open(","pytest","unittest","database","sqlalchemy","pandas","matplotlib","httpx","requests","socket","subprocess","datetime.now","datetime.utcnow","utcnow","date.today","time.time","uuid","random","secrets","threading","multiprocessing","asyncio","concurrent.futures","retry","poll","schedule")
    assert not [x for x in prohibited if x in text]
def test_lower_packages_do_not_import_research_portfolio():
    roots=("app/research_program","app/research_study","app/research_campaign","app/experiment","app/parameter_sweep","app/backtest_suite","app/backtest_report","app/backtest_run","app/analytics","app/trade_journal_batch","app/replay_cycle_projection","app/historical_replay")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert re.search(r"app\.research_portfolio(?:\.|\s|$)",text) is None
