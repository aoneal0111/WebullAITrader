"""Acknowledged entries install native management without another buy."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_paper_entries import GrokPaperEntries, PreparedEntry
from app.configuration.models import PaperSymbolAuthorizationMode
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.momentum_scanner.models import CatalystStatus
from app.strategies.warrior_momentum import (
    FloatProvenance, ForwardCaptureStore, ForwardCaptureWriter, WarriorForwardCaptureService,
)
from app.strategies.warrior_momentum.forward_models import PaperSymbolAuthorization, PaperSymbolAuthorizationSource
from app.strategies.warrior_momentum.models import MomentumEntrySignal, SetupType, StopModel
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig
from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperPaperRuntimeAdapter
from tests.asset_modules.test_controlled_paper_service import setup, fill, NOW
from tests.asset_modules.test_grok_paper_entries import prepared, suite, run


AUTH = PaperSymbolAuthorization(True, PaperSymbolAuthorizationMode.STATIC_ALLOWLIST,
                               PaperSymbolAuthorizationSource.STATIC_ALLOWLIST)


def signal():
    return MomentumEntrySignal("WARRIOR_MOMENTUM_V1", "SPY", NOW, "REGULAR", D(90),
        SetupType.HIGH_OF_DAY_BREAKOUT, D(6), D(6), D("5.9"), StopModel.RECENT_SWING_LOW,
        D("0.1"), (D("6.1"), D("6.2")), CatalystStatus.TRUE, D(5), None, D("0.1"),
        D(1000), D(6000), D(90), (), structural_episode_id="one")


@pytest.fixture
def warrior(setup, tmp_path):
    commands, bridge, _, _, _, _ = setup
    store = ForwardCaptureStore(tmp_path / "forward.db")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    runtime = WarriorForwardCaptureService(store, writer,
        paper_entry_submitter=bridge.submit_entry_decision,
        paper_exit_submitter=bridge.ensure_exit,
        paper_position_quantity_source=bridge._authoritative_quantity,
        paper_order_source=commands.order_book.get,
        paper_acknowledgement_adopter=bridge.adopt_entry_acknowledgement)
    yield runtime
    writer.close()


def reviewed(setup, engine=EngineId.WARRIOR):
    commands, bridge, service, _, _, clock = setup
    ai, _ = suite()
    entry = prepared(commands, engine)
    report = run(GrokPaperEntries(ai, service, authorize=lambda _: True, clock=lambda: clock[0],
        on_acknowledged=lambda e, r: bridge.adopt_entry_acknowledgement(e.placement, r)), [entry])
    assert report.outcomes[0].state == "SUBMITTED"
    return entry, report.outcomes[0].result, ai


def adopt_warrior(runtime, entry, result, value=None):
    return runtime.adopt_acknowledged_entry(value or signal(), entry.placement, result,
        float_provenance=FloatProvenance.UNKNOWN, symbol_authorization=AUTH)


@pytest.mark.parametrize("filled", [0, 3, 10])
def test_warrior_handoff_reconciles_unfilled_partial_and_full_entry(setup, warrior, filled):
    commands, bridge, _, _, _, _ = setup
    entry, result, ai = reviewed(setup)
    if filled:
        commands.gateway.process_market_event(MarketEvent(1, NOW, "SPY", "test", MarketEventType.QUOTE,
            QuotePayload(D("5.99"), D(6), D(filled), D(filled))))
    assert adopt_warrior(warrior, entry, result)
    assert adopt_warrior(warrior, entry, result)
    state = warrior._paper["SPY"]
    assert state.signal == signal()
    assert state.managed_quantity == (filled or 10)
    if filled:
        stops = [o for o in commands.order_book.open_orders_for_symbol("SPY") if o.request.execution_reason == "STOP"]
        assert len(stops) == 1 and stops[0].request.quantity == filled
    # Later fills continue through the existing protection reconciler.
    fill(commands, sequence=2)
    assert warrior.reconcile_authoritative_protection("SPY", NOW)
    stops = [o for o in commands.order_book.open_orders_for_symbol("SPY") if o.request.execution_reason == "STOP"]
    assert len(stops) == 1 and stops[0].request.quantity == 10
    assert len([o for o in commands.order_book.history() if o.request.side.value == "BUY"]) == 1
    assert ai.client.requests == 2


@pytest.mark.parametrize("change", ["symbol", "trigger", "stop", "lifecycle", "result", "source"])
def test_warrior_refuses_mismatched_handoff_without_installing_state(setup, warrior, change):
    entry, result, _ = reviewed(setup)
    value = signal()
    if change == "symbol":
        value = replace(value, symbol="QQQ")
    elif change == "trigger":
        value = replace(value, entry_trigger=D("6.01"))
    elif change == "stop":
        value = replace(value, stop_price=D("5.8"))
    elif change == "lifecycle":
        value = replace(value, structural_episode_id="other")
    elif change == "result":
        result = replace(result, broker_order_id="PAPER-UNKNOWN")
    else:
        warrior._paper_order_source = None
    assert not adopt_warrior(warrior, entry, result, value)
    assert not warrior.open_paper_symbols


def test_warrior_handoff_cannot_reopen_terminal_unfilled_entry(setup, warrior):
    commands, _, _, _, _, _ = setup
    entry, result, _ = reviewed(setup)
    commands.order_book.cancel(result.broker_order_id)
    assert not adopt_warrior(warrior, entry, result)
    assert not warrior.open_paper_symbols


def test_warrior_protection_failure_retains_management_for_retry(setup, warrior):
    commands, _, _, _, _, _ = setup
    entry, result, _ = reviewed(setup)
    fill(commands)
    normal = warrior._paper_exit_submitter
    warrior._paper_exit_submitter = lambda *a, **k: False
    assert adopt_warrior(warrior, entry, result)
    assert not warrior._paper["SPY"].protection_reconciled
    warrior._paper_exit_submitter = normal
    assert warrior.reconcile_authoritative_protection("SPY", NOW)
    assert len([o for o in commands.order_book.history() if o.request.side.value == "BUY"]) == 1


def scalper(setup):
    commands, bridge, _, account, quote, clock = setup
    return QuickScalperPaperRuntimeAdapter(config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=commands.order_book, account_context_source=lambda: account[0],
        position_quantity_source=bridge._authoritative_quantity,
        execution_quote_source=lambda _: quote[0], clock=lambda: clock[0])


@pytest.mark.parametrize("filled", [False, True])
def test_scalper_handoff_uses_native_ledger_reconciliation(setup, filled):
    commands, _, _, _, _, _ = setup
    runtime = scalper(setup)
    entry, result, ai = reviewed(setup, EngineId.SCALPER)
    if filled:
        fill(commands)
    assert runtime.adopt_acknowledged_entry(entry.placement, result)
    assert runtime.adopt_acknowledged_entry(entry.placement, result)
    if not filled:
        fill(commands)
        runtime.reconcile("SPY")
    assert runtime._positions["SPY"].lifecycle_id == entry.candidate.lifecycle_id
    working = commands.order_book.open_orders_for_symbol("SPY")
    assert {o.request.execution_reason for o in working} == {"STOP", "SCALP_TARGET"}
    assert len([o for o in commands.order_book.history() if o.request.side.value == "BUY"]) == 1
    assert ai.client.requests == 2


@pytest.mark.parametrize("target", [None, "NaN", "5.9"])
def test_scalper_missing_or_invalid_target_is_refused_before_ai(setup, target):
    commands, _, _, _, _, _ = setup
    entry = prepared(commands, EngineId.SCALPER)
    p = replace(entry.placement, order=replace(entry.placement.order,
        metadata={**dict(entry.placement.order.metadata), "adaptive_target": target}))
    with pytest.raises(ValueError):
        PreparedEntry(entry.candidate, p)
    assert not commands.order_book.history()
