from types import SimpleNamespace as NS
from decimal import Decimal as D
from app.composition.desktop_trading_state import DesktopTradingStateSources


def source(equity="9900", gross="1000", complete=True):
    account = NS(current_equity=D(equity), buying_power=D("8000"),
                 starting_equity=D("10000"), gross_exposure=D(gross),
                 valuation_complete=complete)
    state = NS(broker_account=None, paper_account=account)
    return DesktopTradingStateSources(state_store=NS(snapshot=lambda: state),
        runtime_projections=NS(), operational_configuration=NS(
            environment=NS(value="PAPER"), allowed_symbols=("XYZ",),
            paper_symbol_authorization_mode="STATIC_ALLOWLIST"))


def test_campaign_loss_gate_survives_new_composition_and_retains_exposure():
    assert source().warrior_account_context().risk_engine_approved
    context = source().warrior_account_context()
    assert context.existing_exposure == D("1000")
    assert context.exposure_limit == D("4950")
    for _ in range(2):
        assert not source("9800").warrior_account_context().risk_engine_approved
    assert not source(gross="6000").warrior_account_context().risk_engine_approved
    assert not source(complete=False).warrior_account_context().risk_engine_approved
