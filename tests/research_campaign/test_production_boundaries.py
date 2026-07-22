from pathlib import Path
import re
def test_only_experiment_application_boundary_is_used():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/research_campaign").glob("*.py"))
    prohibited=("app.parameter_sweep","app.backtest_suite","app.backtest_run","app.backtest_report","historical_replay","replay_cycle_projection","trade_journal_batch","app.analytics","app.strategy","app.risk","execution_planner","paper_trading","execution_orchestrator","trading_cycle","broker","webull","persistence","storage","optimizer","ranking","recommendation","statistics","walk_forward","exporter","chart","pathlib","pytest","unittest","database","sqlalchemy","httpx","requests","socket","datetime.now","utcnow","date.today","time.time","uuid","random","threading","multiprocessing","asyncio","retry","poll")
    assert not [value for value in prohibited if value in text]
def test_lower_domains_do_not_import_research_campaign():
    roots=("app/experiment","app/parameter_sweep","app/backtest_suite","app/backtest_report","app/backtest_run","app/analytics","app/trade_journal_batch","app/replay_cycle_projection","app/historical_replay")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert re.search(r"app\.research_campaign(?:\.|\s|$)",text) is None
