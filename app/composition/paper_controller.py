"""Opt-in desktop PAPER controller, bound to one immutable campaign ledger."""
from hashlib import sha256
from decimal import localcontext

from app.asset_modules.admission_controller import PaperAdmissionController
from app.asset_modules.controlled_paper_service import ControlledPaperTradingService
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperReadiness


def attach_paper_controller(commands, bridge, *, account_source, quote_source, risk_config, clock=None):
    store = commands.durable_store
    if store is None or not commands.paper_campaign_id:
        raise ValueError("PAPER controller requires durable campaign storage")
    capital = store.active_campaign_capital()
    if capital is None:
        raise ValueError("PAPER campaign starting capital unavailable")
    campaign_key = sha256(commands.paper_campaign_id.encode()).hexdigest()
    path = store.path.parent / "paper-controller" / (campaign_key + ".sqlite3")
    controller = PaperAdmissionController(path)
    with localcontext() as ctx:
        ctx.prec = 100
        shared_capital = min(capital["starting_cash"], capital["starting_equity"] * risk_config.maximum_gross_exposure_fraction)
        shared_loss_budget = capital["starting_equity"] * risk_config.maximum_campaign_loss_fraction
    controller.configure_account(commands.account_id, capital=shared_capital, loss_budget=shared_loss_budget)
    service = ControlledPaperTradingService(commands.trading_service, controller, commands.gateway,
        account_id=commands.account_id, session_id=commands.session_id,
        account_source=account_source, quote_source=quote_source, risk_config=risk_config,
        entry_ready=lambda: bridge.enabled and bridge.mode == "PAPER"
            and bridge.readiness is AutonomousPaperReadiness.READY,
        clock=clock)
    # Recovery bookkeeping does not cancel orders, sell positions or alter stops.
    service.reconcile()
    bridge.trading_service = service
    return service
