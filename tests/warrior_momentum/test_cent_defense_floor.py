from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
from app.strategies.warrior_momentum.profit_retention import (
 ProfitRetentionConfig,ProfitRetentionPolicy,ProfitRetentionPosition,ProfitAction)
from app.strategies.warrior_momentum.quick_scalper import StrategyOwner,QuickScalperConfig
from app.equity_overhaul.paper_experiment import create,load,PaperExperiment

NOW=datetime(2026,11,3,14,tzinfo=UTC)


def position():
    return ProfitRetentionPosition(StrategyOwner.QUICK_SCALPER,'XYZ','life',NOW,D('10'),D('9.97'),D('9.97'),D('10.04'))


def test_legacy_tiny_winner_and_explicit_floor_variant():
    legacy=ProfitRetentionPolicy().evaluate(position(),executable_bid=D('10.02'),observed_at=NOW+timedelta(seconds=2))
    assert legacy.action is ProfitAction.TIGHTEN_STOP and legacy.desired_stop==D('10.02')
    policy=ProfitRetentionPolicy(ProfitRetentionConfig(scalp_minimum_defense_profit=D('.05')))
    assert policy.evaluate(position(),executable_bid=D('10.02'),observed_at=NOW+timedelta(seconds=2)).action is ProfitAction.NONE
    mature=replace(position(),peak_executable_bid=D('10.09'))
    assert policy.evaluate(mature,executable_bid=D('10.06'),observed_at=NOW+timedelta(seconds=2)).desired_stop==D('10.06')
    # Floor cannot suppress max-hold liquidation or lower an existing stop.
    assert policy.evaluate(position(),executable_bid=D('9.99'),observed_at=NOW+timedelta(seconds=301)).action is ProfitAction.FULL_EXIT
    assert policy.evaluate(replace(mature,current_stop=D('10.08')),executable_bid=D('10.06'),observed_at=NOW).action is ProfitAction.NONE


def test_versioned_paper_experiment_preserves_original_limits(tmp_path):
    spec=create(tmp_path,clock=lambda:NOW,profile='CENT_DEFENSE_V4')
    spec=load(tmp_path/'experiment.json',clock=lambda:NOW)[1]
    experiment=PaperExperiment(tmp_path/'experiment.json',spec,clock=lambda:NOW)
    c=experiment.scalper_config(QuickScalperConfig())
    assert c.cent_fill_anchored_economics and c.cent_profit_defense_floor==D('.05')
    assert spec['limits']['maximum_initial_risk']=='1'
    assert spec['limits']['maximum_entry_attempts']==50


def test_recovery_uses_entry_metadata_not_new_runtime_configuration():
    from tests.warrior_momentum.test_cent_scalper_profile import seeded
    runtime,bridge=seeded()
    # Simulate persisted V4 entry; a restarted adapter must recover its floor.
    entry=runtime.order_book.get('entry')
    # A real roundtrip through an independent book retains the entry contract.
    from app.paper_trading.order_book import PaperOrderBook
    from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperPaperRuntimeAdapter
    from tests.warrior_momentum.test_quick_scalper_runtime import Quotes,quote,account,NOW as CLOCK
    book=PaperOrderBook()
    book.submit(replace(entry,request=replace(entry.request,metadata={**entry.request.metadata,'scalp_cent_profit_defense_floor':'.05'})))
    restarted=QuickScalperPaperRuntimeAdapter(config=QuickScalperConfig(enabled=True),bridge=bridge,
        order_book=book,account_context_source=account,position_quantity_source=lambda _:D(10),
        execution_quote_source=Quotes(quote()),clock=lambda:CLOCK)
    assert next(iter(restarted._positions.values())).cent_profit_defense_floor==D('.05')
    assert next(iter(runtime._positions.values())).cent_profit_defense_floor is None
