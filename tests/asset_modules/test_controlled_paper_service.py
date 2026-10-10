from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace
import pytest

from app.asset_modules.admission_controller import PaperAdmissionController
from app.asset_modules.controlled_paper_service import ControlledPaperTradingService
from app.composition.paper_controller import attach_paper_controller
from app.configuration.loader import load_configuration
from app.configuration.models import PaperSymbolAuthorizationMode
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.services.order_command_factory import OrderEntryCommand
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.strategies.warrior_momentum.configuration import RiskConfig
from app.strategies.warrior_momentum.execution_quote import ExecutionQuoteSnapshot
from app.strategies.warrior_momentum.forward_models import PaperAccountContext
from tests.test_support.session_clock import create_session_paper_composition, REGULAR_SESSION_UTC as NOW


@pytest.fixture
def setup(tmp_path):
    holder = {}
    def quantity(symbol):
        return sum((o.filled_quantity * (1 if o.request.side.value == "BUY" else -1)
                    for o in holder["commands"].order_book.history() if o.symbol == symbol), D(0))
    commands = create_session_paper_composition(persistence_path=tmp_path / "execution.db",
                                               position_quantity_source=quantity)
    holder["commands"] = commands
    clock = [NOW]
    account = [PaperAccountContext(equity=D(10000), buying_power=D(10000), allowed_symbols=frozenset({"SPY", "QQQ"}),
                                   exposure_limit=D(5000))]
    quote = [ExecutionQuoteSnapshot("SPY", D(6), D("5.99"), D(6), NOW, NOW, NOW)]
    bridge = AutonomousPaperExecutionBridge(commands.trading_service, commands.order_command_factory,
        order_book=commands.order_book, durable_store=commands.durable_store,
        position_quantity_source=quantity, protection_amender=commands.gateway.amend_protective_stop)
    service = attach_paper_controller(commands, bridge, account_source=lambda: account[0],
        quote_source=lambda s: replace(quote[0], symbol=s) if quote[0] else None,
        risk_config=RiskConfig(), clock=lambda: clock[0])
    commands = replace(commands, trading_service=service)
    holder["commands"] = commands
    yield commands, bridge, service, account, quote, clock
    commands.close()


def placement(commands, *, engine="WARRIOR_MOMENTUM_V1", symbol="SPY", side="BUY", reason="ENTRY", **metadata):
    return commands.order_command_factory.create_placement_request(OrderEntryCommand(
        symbol=symbol, side=side, quantity=D(10), order_type="LIMIT", limit_price=D(6), stop_price=None,
        time_in_force="DAY", strategy_lifecycle_id=engine + "|" + symbol + "|one",
        metadata={"source": "autonomous-paper", "reason": reason, "structural_stop": "5.9",
                  "risk_dollars": "1", "opportunity_id": "original-opportunity",
                  "chase_deadline": (NOW + timedelta(seconds=60)).isoformat(), **metadata}))


def fill(commands, *, bid="5.99", ask="6", sequence=1):
    commands.gateway.process_market_event(MarketEvent(sequence, NOW, "SPY", "test", MarketEventType.QUOTE,
        QuotePayload(D(bid), D(ask), D(100), D(100))))


@pytest.mark.parametrize("engine", ["WARRIOR_MOMENTUM_V1", "QUICK_SCALPER"])
def test_real_trading_service_preserves_strategy_and_opportunity_metadata(setup, engine):
    commands, bridge, service, account, quote, clock = setup
    result = service.place_order(placement(commands, engine=engine))
    assert result.success
    order = commands.order_book.history()[0]
    assert order.request.metadata["source"] == "autonomous-paper"
    assert order.request.metadata["opportunity_id"] == "original-opportunity"
    assert order.request.strategy_lifecycle_id.startswith(engine + "|")
    assert order.request.structural_stop_price == D("5.9")
    assert commands.trading_service is service and bridge.trading_service is service
    assert service.status()["reserved_capital"] == 60


def test_bridge_entry_reaches_controller_and_matching_protection_remains_independent(setup):
    commands, bridge, service, account, quote, clock = setup
    signal = SimpleNamespace(symbol="SPY", entry_trigger=D(6), stop_price=D("5.9"), timestamp=NOW,
        strategy_id="WARRIOR_MOMENTUM_V1", lifecycle_id="WARRIOR_MOMENTUM_V1|SPY|one")
    assert bridge.submit_entry(signal, 10, D(1))
    fill(commands)
    assert service.status()["active_commands"] == 1
    # Controller failure and unavailable quote cannot suppress an existing exit.
    service.router.reconcile = lambda: (_ for _ in ()).throw(RuntimeError("ledger unavailable"))
    quote[0] = None
    account[0] = replace(account[0], risk_engine_approved=False)
    result = service.place_order(placement(commands, side="SELL", reason="STOP"))
    assert result.success
    fill(commands, bid="6", ask="6.01", sequence=2)
    assert sum(o.filled_quantity for o in commands.order_book.history() if o.request.side.value == "SELL") == 10


@pytest.mark.parametrize("change,reason", [
    ({"bid_timestamp": NOW - timedelta(seconds=3)}, "PROVIDER_QUOTE_NOT_CURRENT"),
    ({"ask_timestamp": NOW + timedelta(milliseconds=1)}, "PROVIDER_QUOTE_NOT_CURRENT"),
    ({"ask": D("6.01")}, "ENTRY_NOT_EXECUTABLE"),
    ({"bid": D("5.8")}, "ENTRY_NOT_EXECUTABLE"),
])
def test_final_provider_quote_validation_blocks_entry(setup, change, reason):
    commands, bridge, service, account, quote, clock = setup
    quote[0] = replace(quote[0], **change)
    result = service.place_order(placement(commands))
    assert not result.success and service.last_reason == reason
    assert not commands.order_book.history()
    assert service.status()["active_commands"] == 0


@pytest.mark.parametrize("change", [{"risk_engine_approved": False}, {"broker_restriction": True},
                                   {"buying_power": D(1)}, {"exposure_limit": D(1)},
                                   {"allowed_symbols": frozenset()}])
def test_account_authorization_remains_hard(setup, change):
    commands, bridge, service, account, quote, clock = setup
    account[0] = replace(account[0], **change)
    assert not service.place_order(placement(commands)).success
    assert service.last_reason == "RISK_NOT_AUTHORIZED"
    assert not commands.order_book.history()


def test_unmanaged_startup_inventory_blocks_new_entries_without_selling_it(setup):
    commands, bridge, service, account, quote, clock = setup
    raw = service.service
    assert raw.place_order(placement(commands, engine="operator")).success
    fill(commands)
    before = commands.order_book.history()
    assert not service.place_order(placement(commands, symbol="QQQ")).success
    assert service.last_reason == "UNMANAGED_PAPER_EXPOSURE"
    assert commands.order_book.history() == before


def test_failed_controller_never_falls_back_to_raw_buy(setup):
    commands, bridge, service, account, quote, clock = setup
    service.router.reconcile = lambda: (_ for _ in ()).throw(RuntimeError("corrupt controller"))
    assert not service.place_order(placement(commands)).success
    assert service.last_reason == "CONTROLLER_UNAVAILABLE"
    assert not commands.order_book.history()


def test_disabled_bridge_and_addons_or_operator_buys_do_not_bypass(setup):
    commands, bridge, service, account, quote, clock = setup
    bridge.enabled = False
    assert not service.place_order(placement(commands)).success
    assert service.last_reason == "BRIDGE_NOT_READY"
    bridge.enabled = True
    for reason in ("AUTONOMOUS_ADD_ON_ENTRY", "OPERATOR_ENTRY"):
        assert not service.place_order(placement(commands, reason=reason)).success
        assert service.last_reason == "UNSUPPORTED_ENTRY_MUTATION"


def test_dynamic_symbol_modes_keep_scalper_separate(setup):
    commands, bridge, service, account, quote, clock = setup
    account[0] = replace(account[0], allowed_symbols=frozenset(),
                         symbol_authorization_mode=PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR)
    assert not service.place_order(placement(commands, engine="QUICK_SCALPER")).success
    assert service.place_order(placement(commands)).success


def test_campaign_rollover_cannot_reuse_old_controller_or_drop_uncertainty(setup):
    commands, bridge, service, account, quote, clock = setup
    assert service.place_order(placement(commands)).success
    # Stored campaign changes are rejected before any new dispatch.
    service.router.campaign_id = "different-campaign"
    assert not service.place_order(placement(commands, symbol="QQQ")).success
    assert service.last_reason == "CONTROLLER_UNAVAILABLE"
    assert service.status()["active_commands"] == 1


def test_config_controller_flag_defaults_off_and_is_explicit():
    assert load_configuration({}).paper_controller_enabled is False
    assert load_configuration({"ATLAS_PAPER_CONTROLLER_ENABLED": "true"}).paper_controller_enabled is True


def test_unfilled_replacement_reconciles_predecessor_and_reserves_again(setup):
    from app.order_cancellation import OrderCancellationRequest
    commands, bridge, service, account, quote, clock = setup
    first = service.place_order(placement(commands))
    old = commands.order_book.get(first.broker_order_id)
    assert service.cancel_order(OrderCancellationRequest(request_id="cancel", session_id=commands.session_id,
        account_id=commands.account_id, broker_order_id=old.order_id,
        client_order_id=old.request.client_order_id)).success
    replacement = service.place_order(placement(commands, reason="ENTRY_REPLACEMENT", replacement_sequence="1"))
    assert replacement.success
    assert service.status()["active_commands"] == 1
    assert service.status()["reserved_capital"] == 60
    assert commands.order_book.get(replacement.broker_order_id).request.execution_reason == "ENTRY_REPLACEMENT"


def test_filled_predecessor_cannot_gain_an_unreserved_replacement(setup):
    commands, bridge, service, account, quote, clock = setup
    assert service.place_order(placement(commands)).success
    fill(commands)
    assert not service.place_order(placement(commands, reason="ENTRY_REPLACEMENT", replacement_sequence="1")).success
    assert service.last_reason == "LIFECYCLE_ALREADY_USED"
    assert len(commands.order_book.history()) == 1


def test_shared_controller_limits_warrior_and_scalper_together(setup):
    commands, bridge, service, account, quote, clock = setup
    # Use the same real account with a separately bounded admission ledger.
    from tempfile import TemporaryDirectory
    from pathlib import Path
    with TemporaryDirectory() as directory:
        c = PaperAdmissionController(Path(directory) / "bounded.db")
        c.configure_account(commands.account_id, capital=D(60), loss_budget=D(2))
        service.router.controller = c
        assert service.place_order(placement(commands)).success
        assert not service.place_order(placement(commands, engine="QUICK_SCALPER", symbol="QQQ")).success
        assert service.last_reason == "SHARED_CAPITAL_LIMIT"


def test_risk_is_rechecked_after_reservation_before_broker_call(setup):
    commands, bridge, service, account, quote, clock = setup
    original = service.router.controller.reserve
    def reserve(*args, **kwargs):
        result = original(*args, **kwargs)
        account[0] = replace(account[0], risk_engine_approved=False)
        return result
    service.router.controller.reserve = reserve
    assert not service.place_order(placement(commands)).success
    assert service.status()["active_commands"] == 0
    assert not commands.order_book.history()


def test_inactive_desktop_composition_wires_both_engines_and_operator_boundary(monkeypatch, tmp_path):
    from app.composition.desktop import create_desktop_composition
    from app.diagnostics.paper_validation_capture import composition_snapshot
    monkeypatch.setenv("WEBULL_TRADING_ENVIRONMENT", "PAPER")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("ATLAS_PAPER_CONTROLLER_ENABLED", "true")
    monkeypatch.setenv("QUICK_SCALPER_ENABLED", "true")
    composition = create_desktop_composition(paper_persistence_path=tmp_path / "desktop.db", paper_clock=lambda: NOW)
    try:
        service = composition.paper_controller_service
        assert service is not None
        assert composition.trading_service is service
        assert composition.autonomous_paper_bridge.trading_service is service
        assert composition.paper_trading_commands.trading_service is service
        assert composition.quick_scalper_runtime.bridge is composition.autonomous_paper_bridge
        assert not composition.runtime_service.is_active
        status = composition_snapshot(composition)["paper_controller"]
        assert status["enabled"] is True
        assert status["active_commands"] == 0
    finally:
        composition.close()
