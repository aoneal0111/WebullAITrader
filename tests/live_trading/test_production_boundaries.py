from pathlib import Path
import re
def test_only_immediate_public_dependencies_are_imported():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/live_trading").glob("*.py"))
    prohibited=("app.order_placement","app.research_program","app.research_study","app.research_campaign","app.experiment","app.parameter_sweep","app.backtest_suite","app.backtest_run","app.backtest_report","app.historical_replay","app.replay_cycle_projection","app.trade_journal_batch","app.trade_journal","app.analytics","app.strategy","app.risk","app.execution","app.paper_trading","app.execution_orchestrator","app.trading_cycle","app.broker_adapter","app.broker_protocol","app.session","app.storage","app.persistence","app.evidence","webull","pathlib","open(","os.environ","getenv","socket","requests","httpx","subprocess","threading","multiprocessing","asyncio","concurrent.futures","schedule","uuid","random","datetime.now","datetime.utcnow","time.time")
    assert not [x for x in prohibited if x in text]
def test_no_automatic_research_to_order_conversion_or_concrete_runtimes():
    text="\n".join(p.read_text(encoding="utf-8").lower() for p in Path("app/live_trading").glob("*.py"))
    prohibited=("researchportfolioruntime(","deterministicorderplacementruntime(","def generate_order","def build_order","def map_research","symbol=","quantity=","side=")
    assert not [x for x in prohibited if x in text]
def test_lower_layers_do_not_import_live_trading():
    roots=("app/research_portfolio","app/research_program","app/research_study","app/research_campaign","app/experiment","app/parameter_sweep","app/broker","app/order_placement")
    text="\n".join(p.read_text(encoding="utf-8").lower() for root in roots for p in Path(root).glob("*.py"))
    assert re.search(r"app\.live_trading(?:\.|\s|$)",text) is None
