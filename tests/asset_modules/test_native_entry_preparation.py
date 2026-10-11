"""Review preparation preserves native commands without submitting entries."""
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_paper_entries import PreparedEntry
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousPaperReadiness, PaperEntryAuthorizationResult,
)
from tests.asset_modules.test_controlled_paper_service import setup, fill
from tests.test_support.session_clock import REGULAR_SESSION_UTC as NOW


def signal(engine):
    owner = engine.value
    return SimpleNamespace(
        symbol="SPY", entry_trigger=D(6), stop_price=D("5.9"), timestamp=NOW,
        strategy_id=owner, strategy_owner=owner,
        lifecycle_id=owner + "|SPY|native", generation_id="native-generation",
        adaptive_target=D("6.2"), execution_bid=D("5.99"),
        provider_bid_timestamp=NOW, provider_ask_timestamp=NOW,
        taxonomy_strategy_memberships=("HIGH_OF_DAY_BREAKOUT",),
    )


@pytest.mark.parametrize("engine", [EngineId.WARRIOR, EngineId.SCALPER])
def test_preparation_is_not_authorization_submission_or_durable_claim(setup, engine):
    commands, bridge, service, _, _, _ = setup
    before = service.status()
    original = service.place_order
    service.place_order = Mock(side_effect=AssertionError("Preparation submitted a buy"))
    value = signal(engine)
    result = bridge.prepare_entry_decision(value, 10, D(1), opportunity_id="native-opportunity")
    assert result.decision.result is PaperEntryAuthorizationResult.PREPARED
    assert not result.decision.authorized
    assert result.decision.order_constructed and not result.decision.submission_attempted
    assert result.decision.placement_decision is None
    assert not service.place_order.called
    assert not commands.order_book.history()
    assert not bridge._active_by_symbol and not bridge._entry_orders
    assert not bridge._identity_seen(result.decision.lifecycle_id)
    assert commands.durable_store.consumed_opportunity("native-opportunity") is None
    assert service.status() == before
    prepared = PreparedEntry.from_placement(result.placement, evidence={"setup": "TRIGGERED"})
    assert prepared.placement is result.placement
    assert prepared.candidate.engine is engine
    service.place_order = original
    # A prepared command is still subject to the controller at actual dispatch.
    placement_result = service.place_order(result.placement)
    assert placement_result.success
    assert bridge.adopt_entry_acknowledgement(result.placement, placement_result)
    fill(commands)
    bridge.reconcile_protection()
    assert len([o for o in commands.order_book.history() if o.request.side.value == "BUY"]) == 1
    assert any(o.request.execution_reason == "STOP" for o in commands.order_book.history())


@pytest.mark.parametrize("engine", [EngineId.WARRIOR, EngineId.SCALPER])
def test_preparation_and_native_submission_use_same_order_builder(setup, engine):
    _, bridge, service, _, _, _ = setup
    value = signal(engine)
    options = dict(opportunity_id="native-opportunity", opportunity_anchor=D(6),
        opportunity_deadline=NOW + timedelta(seconds=45), lifecycle_number=2,
        max_lifecycles_per_opportunity=4, provenance="NATIVE_REVIEW")
    preparation = bridge.prepare_entry_decision(value, 10, D(1), **options)
    captured = []
    original = service.place_order
    def place(request):
        captured.append(request)
        return original(request)
    service.place_order = place
    decision = bridge.submit_entry_decision(value, 10, D(1), **options)
    assert decision.authorized and len(captured) == 1
    prepared, submitted = preparation.placement.order, captured[0].order
    assert dict(prepared.metadata) == dict(submitted.metadata)
    for field in ("symbol", "side", "quantity", "order_type", "limit_price", "stop_price",
                  "time_in_force", "strategy_lifecycle_id", "account_id"):
        assert getattr(prepared, field) == getattr(submitted, field)


@pytest.mark.parametrize("change,reason", [
    ("disabled", "PAPER_DISABLED"), ("live", "ENVIRONMENT_MISMATCH"),
    ("readiness", "BROKER_NOT_READY"), ("quantity", "INVALID_QUANTITY"),
    ("duplicate", "DUPLICATE_LIFECYCLE"), ("position", "POSITION_EXISTS"),
    ("working", "WORKING_ORDER_EXISTS"), ("displacement", "EXPOSURE_LIMIT"),
    ("factory", "ORDER_CONSTRUCTION_FAILED"),
])
def test_preparation_reuses_native_refusals_without_placing(setup, change, reason):
    commands, bridge, service, _, _, _ = setup
    value = signal(EngineId.WARRIOR)
    kwargs = {}
    shares = 10
    if change == "disabled":
        bridge.enabled = False
    elif change == "live":
        bridge.mode = "LIVE"
    elif change == "readiness":
        bridge._readiness = AutonomousPaperReadiness.BLOCKED
    elif change == "quantity":
        shares = 0
    elif change == "duplicate":
        bridge._remember(bridge._seen_entries, value.lifecycle_id)
    elif change == "position":
        bridge._active_by_symbol["SPY"] = "existing-owner"
    elif change == "working":
        assert bridge.submit_entry_decision(value, shares, D(1)).authorized
    elif change == "displacement":
        kwargs["opportunity_anchor"] = D(5)
    elif change == "factory":
        bridge.order_command_factory = Mock()
        bridge.order_command_factory.create_placement_request.side_effect = ValueError("bad command")
    before = commands.order_book.history()
    service.place_order = Mock(side_effect=AssertionError("Refusal submitted an order"))
    result = bridge.prepare_entry_decision(value, shares, D(1), **kwargs)
    assert result.placement is None
    assert result.decision.result is PaperEntryAuthorizationResult.REFUSED
    assert result.decision.reason.value == reason
    assert not result.decision.authorized and not result.decision.submission_attempted
    assert not service.place_order.called
    assert commands.order_book.history() == before


@pytest.mark.parametrize("change", ["quote", "account"])
def test_prepared_command_does_not_bypass_later_controller_revocation(setup, change):
    commands, bridge, service, account, quote, _ = setup
    from dataclasses import replace
    result = bridge.prepare_entry_decision(signal(EngineId.WARRIOR), 10, D(1))
    if change == "quote":
        quote[0] = None
    else:
        account[0] = replace(account[0], risk_engine_approved=False)
    assert not service.place_order(result.placement).success
    assert not commands.order_book.history()
    assert not bridge._active_by_symbol


@pytest.mark.parametrize("engine", [EngineId.WARRIOR, EngineId.SCALPER])
def test_fresh_native_command_passes_mock_engine_and_master_review(setup, engine):
    from app.asset_modules.grok_paper_entries import GrokPaperEntries
    from tests.asset_modules.test_grok_paper_entries import suite, run
    commands, bridge, service, _, _, clock = setup
    preparation = bridge.prepare_entry_decision(signal(engine), 10, D(1))
    entry = PreparedEntry.from_placement(preparation.placement, evidence={"setup": "TRIGGERED"})
    ai, _ = suite()
    dispatcher = GrokPaperEntries(ai, service, authorize=lambda _: True,
        clock=lambda: clock[0], on_acknowledged=lambda e, r:
            bridge.adopt_entry_acknowledgement(e.placement, r))
    report = run(dispatcher, [entry])
    assert report.outcomes[0].state == "SUBMITTED"
    assert report.outcomes[0].result.success
    assert ai.client.requests == 2
    assert len(commands.order_book.history()) == 1
    assert bridge._entry_orders[entry.candidate.lifecycle_id] == report.outcomes[0].result.broker_order_id


def test_candidate_deadline_matches_native_opportunity_without_rewriting(setup):
    from dataclasses import replace
    commands, bridge, service, _, _, _ = setup
    preparation = bridge.prepare_entry_decision(signal(EngineId.WARRIOR), 10, D(1))
    placement = preparation.placement
    entry = PreparedEntry.from_placement(placement, evidence={})
    assert "chase_deadline" not in placement.order.metadata
    assert entry.candidate.valid_until == NOW + timedelta(seconds=60)
    with pytest.raises(ValueError, match="deadline conflict"):
        PreparedEntry(replace(entry.candidate, valid_until=NOW + timedelta(seconds=61)), placement)
    result = service.place_order(placement)
    assert result.success
    forged = replace(placement, order=replace(placement.order,
        metadata={**dict(placement.order.metadata),
            "opportunity_deadline": (NOW + timedelta(seconds=61)).isoformat()}))
    assert not bridge.adopt_entry_acknowledgement(forged, result)
    assert bridge.adopt_entry_acknowledgement(placement, result)


def test_preparation_diagnostics_do_not_report_submission_or_authorization(setup, monkeypatch):
    import app.strategies.warrior_momentum.autonomous_paper as module
    from app.performance_diagnostics import PerformanceDiagnostics
    diagnostics = PerformanceDiagnostics()
    monkeypatch.setattr(module, "performance_diagnostics", diagnostics)
    _, bridge, _, _, _, _ = setup
    assert bridge.prepare_entry_decision(signal(EngineId.WARRIOR), 10, D(1)).placement
    report = diagnostics.entry_conversion_metrics()
    assert report["counters"]["entry_preparations"] == 1
    assert report["counters"]["entry_authorizations"] == 0
    assert report["counters"]["entry_orders_submitted"] == 0
    assert report["funnel_counts"]["ENTRY_PREPARATION"] == 1
    for stage in ("PAPER_AUTHORIZATION", "ORDER_SUBMITTED", "ORDER_ACKNOWLEDGED"):
        assert report["funnel_counts"][stage] == 0
    assert report["funnel_records"][-1]["outcome"] == "PREPARED"
