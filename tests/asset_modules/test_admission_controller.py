from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, UTC
from decimal import Decimal as D, localcontext
import multiprocessing
import pytest

from app.asset_modules.admission_controller import EntryReservation, PaperAdmissionController
from app.asset_modules.engine_catalog import EngineId

NOW = datetime(2026, 10, 10, 15, tzinfo=UTC)


def request(number="one", **kwargs):
    defaults = dict(command_id=number, account_id="paper-account", engine=EngineId.SCALPER,
                    lifecycle_id=f"QUICK_SCALPER|SPY|{number}", instrument="SPY",
                    side="BUY", quantity=D("10"), entry_limit=D("6"),
                    structural_stop=D("5.9"), multiplier=D("1"),
                    policy_version="paper-v1", capital=D("60"), loss_budget=D("10"),
                    quote_at=NOW, expires_at=NOW + timedelta(seconds=30))
    defaults.update(kwargs)
    return EntryReservation(**defaults)


@pytest.fixture
def controller(tmp_path):
    value = PaperAdmissionController(tmp_path / "controller.sqlite3")
    value.configure_account("paper-account", capital=D("100"), loss_budget=D("15"))
    return value


def test_shared_capital_and_loss_budget_with_instrument_ownership(controller):
    assert controller.reserve(request(), now=NOW).state == "RESERVED"
    warrior = request("warrior", engine=EngineId.WARRIOR,
                      lifecycle_id="WARRIOR_MOMENTUM_V1|SPY|warrior")
    assert controller.reserve(warrior, now=NOW).reason == "INSTRUMENT_ALREADY_RESERVED"
    other = request("two", instrument="QQQ")
    assert controller.reserve(other, now=NOW).reason == "SHARED_CAPITAL_LIMIT"
    assert controller.reserve(replace(other, capital=D("30"), quantity=D("5")), now=NOW).reason == "SHARED_LOSS_BUDGET"
    assert controller.reserve(replace(other, capital=D("30"), quantity=D("5"), loss_budget=D("5")), now=NOW).state == "RESERVED"
    assert controller.snapshot("paper-account")["reserved_capital"] == D("90")


def test_same_id_cannot_be_mutated_or_dispatched_twice(controller):
    item = request()
    assert controller.reserve(item, now=NOW).state == "RESERVED"
    assert controller.reserve(replace(item, capital=D("60.00")), now=NOW).reason == "EXISTING_COMMAND"
    for changed in (replace(item, capital=D("61")), replace(item, policy_version="v2"),
                    replace(item, instrument="QQQ"), replace(item, quantity=D("9")),
                    replace(item, entry_limit=D("5.99"))):
        with pytest.raises(ValueError, match="identity reused"):
            controller.reserve(changed, now=NOW)
    def dispatch(_):
        return PaperAdmissionController(controller.path).claim_dispatch(item.command_id, item.engine, now=NOW)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(dispatch, range(16))) == 1
    assert controller.snapshot(item.account_id)["uncertain_submissions"] == 1


def test_crash_recovery_retains_unknown_submission_and_terminal_id(controller):
    item = request()
    controller.reserve(item, now=NOW)
    assert controller.claim_dispatch(item.command_id, item.engine, now=NOW)
    restarted = PaperAdmissionController(controller.path)
    assert not restarted.claim_dispatch(item.command_id, item.engine, now=NOW + timedelta(days=1))
    assert restarted.snapshot(item.account_id)["reserved_capital"] == D("60")
    inventory = restarted.recovery_commands(item.account_id, limit=1)
    assert inventory[0]["state"] == "SUBMITTING"
    assert inventory[0]["request"]["command_id"] == item.command_id
    assert restarted.recovery_commands(item.account_id, after_command_id=item.command_id) == ()
    with pytest.raises(ValueError, match="reconciliation"):
        restarted.abandon_unsubmitted(item.command_id, item.engine)
    for terminal, qty in ((False, D(0)), (True, D(1))):
        with pytest.raises(ValueError):
            restarted.reconcile_flat(item.command_id, item.engine, orders_terminal=terminal,
                                     remaining_quantity=qty, reference="broker-snapshot-1")
    restarted.reconcile_flat(item.command_id, item.engine, orders_terminal=True,
                             remaining_quantity=D(0), reference="broker-snapshot-1")
    assert restarted.snapshot(item.account_id)["active_commands"] == 0
    assert restarted.reserve(item, now=NOW).state == "CLOSED"
    assert not restarted.claim_dispatch(item.command_id, item.engine, now=NOW)


def test_acknowledgment_keeps_reservation_until_position_and_orders_are_flat(controller):
    item = request()
    controller.reserve(item, now=NOW)
    with pytest.raises(ValueError):
        controller.acknowledge(item.command_id, item.engine, "order-1")
    controller.claim_dispatch(item.command_id, item.engine, now=NOW)
    controller.acknowledge(item.command_id, item.engine, "order-1")
    controller.acknowledge(item.command_id, item.engine, "order-1")
    with pytest.raises(ValueError):
        controller.acknowledge(item.command_id, item.engine, "order-2")
    assert controller.snapshot(item.account_id)["reserved_loss_budget"] == D("10")


@pytest.mark.parametrize("offset", [-1, 3])
def test_future_or_old_quote_rejected_before_reservation(controller, offset):
    result = controller.reserve(request(quote_at=NOW - timedelta(seconds=offset)), now=NOW)
    assert result.reason == "QUOTE_NOT_CURRENT"
    assert controller.snapshot("paper-account")["active_commands"] == 0


def test_expiry_cannot_release_a_claimed_order(controller):
    item = request()
    controller.reserve(item, now=NOW)
    assert not controller.claim_dispatch(item.command_id, item.engine, now=NOW + timedelta(seconds=3))
    assert controller.reserve(item, now=NOW).state == "ABANDONED"
    assert controller.snapshot(item.account_id)["active_commands"] == 0


def test_expired_and_overlong_entry_lease_rejected(controller):
    for expires in (NOW, NOW + timedelta(seconds=61)):
        assert controller.reserve(request(expires_at=expires), now=NOW).reason == "ENTRY_NOT_CURRENT"


def test_unsubmitted_reservation_can_be_abandoned_but_id_cannot_be_reused(controller):
    item = request()
    controller.reserve(item, now=NOW)
    controller.abandon_unsubmitted(item.command_id, item.engine)
    assert controller.snapshot(item.account_id)["active_commands"] == 0
    assert controller.reserve(item, now=NOW).state == "ABANDONED"
    assert controller.reserve(request("next"), now=NOW).state == "RESERVED"


def test_recovery_queries_are_account_scoped_and_bounded(controller):
    controller.reserve(request(), now=NOW)
    assert controller.recovery_commands("other-account") == ()
    for limit in (0, 1001, True):
        with pytest.raises(ValueError):
            controller.recovery_commands("paper-account", limit=limit)


def test_other_engine_cannot_operate_owner_command(controller):
    item = request()
    controller.reserve(item, now=NOW)
    for action in (lambda: controller.claim_dispatch(item.command_id, EngineId.WARRIOR, now=NOW),
                   lambda: controller.abandon_unsubmitted(item.command_id, EngineId.WARRIOR),
                   lambda: controller.acknowledge(item.command_id, EngineId.WARRIOR, "order-1"),
                   lambda: controller.reconcile_flat(item.command_id, EngineId.WARRIOR,
                       orders_terminal=True, remaining_quantity=D(0), reference="snapshot")):
        with pytest.raises(ValueError, match="this engine"):
            action()


def test_configure_account_cannot_silently_change_limits(controller):
    controller.configure_account("paper-account", capital=D("100.00"), loss_budget=D("15"))
    with pytest.raises(ValueError):
        controller.configure_account("paper-account", capital=D("200"), loss_budget=D("15"))
    assert controller.reserve(request(account_id="unknown"), now=NOW).reason == "ACCOUNT_NOT_CONFIGURED"


def test_option_multiplier_applies_to_price_risk_and_notional(controller):
    controller.configure_account("options-paper", capital=D("1000"), loss_budget=D("100"))
    option = request("option", account_id="options-paper", engine=EngineId.OPTIONS,
                     lifecycle_id="OPTIONS|SPY|option", instrument="SPY:CALL:CONTRACT-1",
                     quantity=D("1"), multiplier=D("100"), capital=D("600"))
    assert controller.reserve(option, now=NOW).state == "RESERVED"
    assert controller.snapshot("options-paper")["reserved_capital"] == D("600")
    with pytest.raises(ValueError):
        replace(option, loss_budget=D("9.99"))
    with pytest.raises(ValueError):
        replace(option, capital=D("6"))


def test_small_caller_decimal_context_cannot_hide_overallocation(controller):
    controller.reserve(request(), now=NOW)
    other = request("two", instrument="QQQ", quantity=D("5"), capital=D("40.01"), loss_budget=D("1"))
    with localcontext() as context:
        context.prec = 1
        assert controller.reserve(other, now=NOW).reason == "SHARED_CAPITAL_LIMIT"


def test_sell_entry_requires_stop_above_entry(controller):
    short = request(side="SELL", structural_stop=D("6.1"))
    assert controller.reserve(short, now=NOW).state == "RESERVED"
    with pytest.raises(ValueError):
        request(side="SELL")


@pytest.mark.parametrize("changes", [dict(capital=D("NaN")), dict(loss_budget=D("-1")),
    dict(capital=D("1E99")), dict(capital=D("1E-100000")), dict(instrument="spy"),
    dict(engine=EngineId.CRYPTO), dict(command_id=""), dict(quote_at=NOW.replace(tzinfo=None)),
    dict(quantity=D("0")), dict(structural_stop=D("6.1")), dict(loss_budget=D("0.5")),
    dict(capital=D("1"))])
def test_invalid_requests_rejected(changes):
    with pytest.raises(ValueError):
        request(**changes)


def _process_reserve(path, number, output):
    # Import-safe spawn entry point: no GUI, broker calls or Atlas startup.
    controller = PaperAdmissionController(path)
    output.put(controller.reserve(request(number, instrument=number.upper()), now=NOW).state)


def test_spawned_workers_cannot_overallocate_shared_account(controller):
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    processes = [context.Process(target=_process_reserve, args=(controller.path, name, output))
                 for name in ("first", "second")]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            assert not process.is_alive()
            assert process.exitcode == 0
        assert sorted(output.get(timeout=5) for _ in processes) == ["REJECTED", "RESERVED"]
        assert controller.snapshot("paper-account")["reserved_capital"] == D("60")
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        output.close()
