from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import pytest

from app.asset_modules.admission_controller import EntryReservation, PaperAdmissionController
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.paper_controller_router import PaperControllerRouter
from app.paper_gateway import PaperOrderGateway
from app.paper_gateway.durable_store import DurablePaperExecutionStore
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.orders import apply_fill, cancel_order
from app.order_placement import OrderSide

NOW = datetime(2026, 10, 9, 15, tzinfo=UTC)


def entry():
    return EntryReservation(command_id="one", account_id="paper", engine=EngineId.WARRIOR,
        lifecycle_id="WARRIOR_MOMENTUM_V1|SPY|one", instrument="SPY", policy_version="v1",
        side="BUY", quantity=D(10), entry_limit=D(6), structural_stop=D("5.9"),
        multiplier=D(1), capital=D(60), loss_budget=D(2), quote_at=NOW,
        expires_at=NOW + timedelta(seconds=30))


@pytest.fixture
def setup(tmp_path):
    c = PaperAdmissionController(tmp_path / "controller.db")
    c.configure_account("paper", capital=D(100), loss_budget=D(10))
    store = DurablePaperExecutionStore(tmp_path / "execution.db", account_id="paper")
    g = PaperOrderGateway(PaperOrderBook(), clock=lambda: NOW, durable_store=store)
    router = PaperControllerRouter(c, g, account_id="paper", session_id="session",
                                   authorize=lambda r: True, clock=lambda: NOW)
    return c, g, router


def test_routes_actual_gateway_preserves_identity_and_never_duplicates(setup):
    c, g, router = setup
    assert router.submit(entry()).state == "ACKNOWLEDGED"
    order = g.order_book.history()[0]
    assert order.request.structural_stop_price == D("5.9")
    assert order.request.entry_valid_until == entry().expires_at
    assert order.request.execution_reason == "ENTRY"
    assert router.submit(entry()).state == "ACKNOWLEDGED"
    assert len(g.order_book.history()) == 1
    assert router.reconcile() == {"one": "HELD"}
    assert c.snapshot("paper")["reserved_capital"] == 60


def test_uncertain_claim_recovers_order_without_resubmission(setup):
    c, g, router = setup
    original = c.acknowledge
    c.acknowledge = lambda *args: (_ for _ in ()).throw(RuntimeError("crash after broker"))
    with pytest.raises(RuntimeError):
        router.submit(entry())
    c.acknowledge = original
    assert c.snapshot("paper")["uncertain_submissions"] == 1
    assert router.reconcile() == {"one": "HELD"}
    assert c.snapshot("paper")["uncertain_submissions"] == 0
    assert len(g.order_book.history()) == 1


def test_absent_claim_never_released_or_retried(setup):
    c, g, router = setup
    c.reserve(entry(), now=NOW)
    c.claim_dispatch("one", EngineId.WARRIOR, now=NOW)
    assert router.reconcile() == {"one": "UNCERTAIN"}
    assert router.submit(entry()).state == "SUBMITTING"
    assert not g.order_book.history()
    assert c.snapshot("paper")["reserved_capital"] == 60


def test_cancelled_partial_entry_retains_budget_until_exit_terminal(setup):
    c, g, router = setup
    router.submit(entry())
    buy = g.order_book.history()[0]
    buy = cancel_order(apply_fill(buy, D(4), D(6), at=NOW), at=NOW)
    g.order_book.update(buy)
    assert router.reconcile() == {"one": "HELD"}
    # Tests inject authoritative immutable order updates under an inactive gateway.
    sell = replace(buy, order_id="exit", filled_quantity=D(0), fills=(), average_fill_price=None,
        request=replace(buy.request, side=type(buy.request.side).SELL, quantity=D(4),
                        client_order_id="exit", execution_reason="STOP"),
        status=type(buy.status).ACCEPTED, terminal_reason=None)
    g.order_book.submit(sell)
    assert router.reconcile() == {"one": "HELD"}
    g.order_book.update(apply_fill(sell, D(4), D("5.9"), at=NOW))
    assert router.reconcile() == {"one": "CLOSED"}
    assert c.snapshot("paper")["active_commands"] == 0


def test_conflicting_identity_does_not_release(setup):
    c, g, router = setup
    router.submit(entry())
    order = g.order_book.history()[0]
    g.order_book.update(replace(order, request=replace(order.request, limit_price=D("6.1"))))
    assert router.reconcile() == {"one": "IDENTITY_CONFLICT"}
    assert c.snapshot("paper")["active_commands"] == 1


def test_existing_unmanaged_working_order_blocks_new_ingress(setup):
    c, g, router = setup
    router.submit(entry())
    order = g.order_book.history()[0]
    unmanaged = replace(order, order_id="outside", request=replace(order.request,
                        strategy_lifecycle_id=None, client_order_id="outside"))
    g.order_book.submit(unmanaged)
    assert router.submit(entry()).reason == "UNMANAGED_PAPER_EXPOSURE"


def test_authorization_checked_again_before_claim(setup):
    c, g, router = setup
    answers = iter([True, False])
    router.authorize = lambda r: next(answers)
    assert router.submit(entry()).state == "ABANDONED"
    assert not g.order_book.history()
    assert c.snapshot("paper")["active_commands"] == 0


@pytest.mark.parametrize("changes", [{"side": "SELL", "structural_stop": D("6.1")},
    {"quantity": D("1.5")}, {"multiplier": D(100), "capital": D(6000), "loss_budget": D(100)},
    {"account_id": "other"}])
def test_unsupported_contracts_accounts_and_short_entries_rejected(setup, changes):
    with pytest.raises(ValueError):
        setup[2].submit(replace(entry(), **changes))


def test_volatile_or_wrong_account_gateway_rejected(tmp_path):
    c = PaperAdmissionController(tmp_path / "c.db")
    with pytest.raises(ValueError):
        PaperControllerRouter(c, PaperOrderGateway(PaperOrderBook()), account_id="paper",
            session_id="s", authorize=lambda r: True)


def test_restart_recovers_durable_order_and_releases_cancelled_unfilled_entry(setup, tmp_path):
    from app.order_cancellation import OrderCancellationRequest
    c, g, router = setup
    router.submit(entry())
    order = g.order_book.history()[0]
    g.cancel_order(OrderCancellationRequest(request_id="cancel", session_id="session",
        account_id="paper", broker_order_id=order.order_id, client_order_id=order.request.client_order_id))
    store = DurablePaperExecutionStore(tmp_path / "execution.db", account_id="paper")
    restored = PaperOrderGateway(PaperOrderBook(), durable_store=store, clock=lambda: NOW)
    restarted = PaperControllerRouter(PaperAdmissionController(tmp_path / "controller.db"), restored,
        account_id="paper", session_id="session", authorize=lambda r: True, clock=lambda: NOW)
    assert restarted.reconcile() == {"one": "CLOSED"}
    assert c.snapshot("paper")["active_commands"] == 0
    assert restarted.submit(entry()).state == "CLOSED"
    assert len(restored.order_book.history()) == 1


def test_flat_fills_with_outstanding_protection_keeps_reservation(setup):
    c, g, router = setup
    router.submit(entry())
    order = g.order_book.history()[0]
    g.order_book.update(cancel_order(order, at=NOW))
    protection = replace(order, order_id="protection", request=replace(order.request,
        client_order_id="protection", side=type(order.request.side).SELL, execution_reason="STOP"))
    g.order_book.submit(protection)
    assert router.reconcile() == {"one": "HELD"}
    assert c.snapshot("paper")["reserved_capital"] == 60
    g.order_book.update(cancel_order(protection, at=NOW))
    assert router.reconcile() == {"one": "CLOSED"}
