from pathlib import Path
import re
def test_only_research_campaign_boundary_is_used():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/research_study").glob("*.py"))
    prohibited=("app.experiment","app.parameter_sweep","app.backtest_suite","app.backtest_run","app.backtest_report","historical_replay","replay_cycle_projection","trade_journal_batch","app.analytics","app.strategy","app.risk","execution_planner","paper_trading","execution_orchestrator","trading_cycle","broker","webull","persistence","storage","optimizer","ranking","recommendation","statistics","exporter","chart","pathlib","pytest","unittest","database","sqlalchemy","httpx","requests","socket","datetime.now","utcnow","date.today","time.time","uuid","random","threading","multiprocessing","asyncio","retry","poll")
    assert not [x for x in prohibited if x in text]
def test_lower_packages_do_not_import_research_study():
    roots=("app/research_campaign","app/experiment","app/parameter_sweep","app/backtest_suite","app/backtest_report","app/backtest_run","app/analytics","app/trade_journal_batch","app/replay_cycle_projection","app/historical_replay")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert re.search(r"app\.research_study(?:\.|\s|$)",text) is None
