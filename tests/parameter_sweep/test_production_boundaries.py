from pathlib import Path
def test_only_backtest_suite_boundary_is_used():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/parameter_sweep").glob("*.py"))
    prohibited=("app.backtest_run","app.backtest_report","historical_replay","replay_cycle_projection","trade_journal_batch","app.analytics","strategy","risk","execution_planner","paper_trading","execution_orchestrator","broker","webull","persistence","storage","optimization","ranking","exporter","chart","pathlib","open(","database","sqlalchemy","httpx","requests","socket","datetime.now","utcnow","date.today","time.time","uuid","random","threading","multiprocessing","asyncio","retry","poll")
    assert not [x for x in prohibited if x in text]
def test_no_reverse_dependency():
    roots=("app/backtest_suite","app/backtest_report","app/backtest_run","app/analytics","app/trade_journal_batch","app/replay_cycle_projection","app/historical_replay")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert "app.parameter_sweep" not in text
