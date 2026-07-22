from pathlib import Path
import re
def test_only_parameter_sweep_boundary_is_used():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/experiment").glob("*.py"))
    prohibited=("app.backtest_suite","app.backtest_run","app.backtest_report","historical_replay","replay_cycle_projection","trade_journal_batch","app.analytics","strategy","risk","execution_planner","paper_trading","execution_orchestrator","broker","webull","persistence","storage","optimizer","ranking","recommendation","statistics","exporter","chart","pathlib","open(","database","sqlalchemy","httpx","requests","socket","datetime.now","utcnow","date.today","time.time","uuid","random","threading","multiprocessing","asyncio","retry","poll")
    assert not [value for value in prohibited if value in text]
def test_no_reverse_dependency():
    roots=("app/parameter_sweep","app/backtest_suite","app/backtest_report","app/backtest_run","app/analytics","app/trade_journal_batch","app/replay_cycle_projection","app/historical_replay")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert re.search(r"app\.experiment(?:\.|\s|$)",text) is None
