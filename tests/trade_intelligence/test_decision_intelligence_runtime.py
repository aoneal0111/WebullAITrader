from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
import sqlite3
from dataclasses import replace

from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, ScannerObservation,
)
from app.strategies.warrior_momentum.forward_models import (
    PointInTimeObservation,
)
from app.strategies.warrior_momentum.models import (
    MinuteBar, SetupDetection, SetupState, SetupType, StopModel,
)
from app.trade_intelligence.decision_intelligence import HistoricalDecisionIntelligence
from app.opportunity_discovery import MultiStrategyDiscoveryEngine, default_registry
from app.trade_intelligence.taxonomy_paper_bridge import _discovery_context
from app.opportunity_discovery.context import build_impulse, structural_anchor


NOW = datetime(2026, 8, 10, 14, 35, tzinfo=UTC)


def _value(price: str = "10") -> PointInTimeObservation:
    observation = ScannerObservation(
        symbol="XYZ", timestamp=NOW, price=D(price), previous_close=D("8"),
        current_volume=D("1000000"), average_30_day_volume=D("100000"),
        float_shares=D("6000000"), bid=D(price) - D(".01"),
        ask=D(price) + D(".01"), catalyst=CatalystType.EARNINGS,
        catalyst_headline="fixture", tradable=True, halted=False,
        asset_class=AssetClass.STOCK, catalyst_status=CatalystStatus.TRUE,
    )
    bars = tuple(
        MinuteBar("XYZ", NOW - timedelta(minutes=4 - i), D("9.8"), D("10"),
                  D("9.7"), D("9.9"), D("100")) for i in range(5)
    )
    return PointInTimeObservation(
        observation=observation, session="REGULAR", bars=bars,
        quote_freshness_seconds=D("0"), last_price_freshness_seconds=D("0"),
        quote_observed_at=NOW, last_price_observed_at=NOW,
    )


def _candidate(state: SetupState, price: str = "10") -> SimpleNamespace:
    setup = SetupDetection(
        SetupType.HIGH_OF_DAY_BREAKOUT, state, D("80"), D("10.10"),
        D("9.80"), StopModel.BREAKOUT_LEVEL,
        taxonomy_strategy_memberships=("HIGH_OF_DAY_BREAKOUT", "FLAT_TOP_BREAKOUT"),
        taxonomy_opportunity_id="fixture-opportunity",
    )
    return SimpleNamespace(setup=setup, price=D(price))


def test_runtime_is_read_only_and_retains_memberships(tmp_path: Path):
    service = HistoricalDecisionIntelligence(
        journal_path=tmp_path / "journal.sqlite3",
    )
    service.start("PAPER")
    assert service.available
    result = service.evaluate(value=_value(), candidate=_candidate(SetupState.FORMING))
    assert {
        "HIGH_OF_DAY_BREAKOUT", "FLAT_TOP_BREAKOUT",
    } <= set(result.recognized_memberships)
    assert len(result.recognized_memberships) == len(set(result.recognized_memberships))
    assert result.setup_stage == "TRIGGER_READY"
    assert result.readiness_memberships
    assert all(row["state"] == "TRIGGER_ARMED" for row in result.readiness_memberships)
    connection = sqlite3.connect(
        "file:" + str(service.artifact_path.resolve()).replace("\\", "/") + "?mode=ro",
        uri=True,
    )
    try:
        try:
            connection.execute("CREATE TABLE should_fail(value TEXT)")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("artifact connection was not read-only")
    finally:
        connection.close()
    service.close()


def test_missing_or_wrong_artifact_falls_back_without_runtime_failure(tmp_path: Path):
    missing = HistoricalDecisionIntelligence(tmp_path / "missing.sqlite3")
    missing.start("PAPER")
    result = missing.evaluate(value=object(), candidate=object())
    assert not missing.available
    assert result.entry_assessment == "NO_HISTORICAL_INTELLIGENCE"


def test_live_never_opens_historical_artifact(tmp_path: Path):
    service = HistoricalDecisionIntelligence(tmp_path / "missing.sqlite3")
    service.start("LIVE")
    assert not service.available
    assert service._connection is None


def test_meaningful_stage_changes_are_journaled_but_duplicate_ticks_are_deduped(tmp_path: Path):
    service = HistoricalDecisionIntelligence(journal_path=tmp_path / "journal.sqlite3")
    service.start("PAPER")
    service.observe_decision(value=_value("9.95"), candidate=_candidate(SetupState.FORMING, "9.95"))
    service.observe_decision(value=_value("9.95"), candidate=_candidate(SetupState.FORMING, "9.95"))
    service.observe_decision(value=_value("10.20"), candidate=_candidate(SetupState.TRIGGERED, "10.20"))
    service.close()
    with sqlite3.connect(tmp_path / "journal.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM intelligence_observations"
        ).fetchone()[0] == 3


def _event(service, opportunity, kind, at, price, source="TAXONOMY", payload=None, **extra):
    service.record_event(
        opportunity_id=opportunity, event_type=kind, observed_at=at,
        symbol="XYZ", source=source, price=D(price), trigger_price=D("10"),
        payload=payload or {}, **extra,
    )


def test_durable_timeline_distinguishes_saw_early_but_waited(tmp_path: Path):
    service = HistoricalDecisionIntelligence(journal_path=tmp_path / "journal.sqlite3")
    service.start("PAPER")
    opportunity = "early-opportunity"
    _event(service, opportunity, "FIRST_RECOGNIZED", NOW, "9.50")
    _event(service, opportunity, "STRUCTURE_PRESENT", NOW + timedelta(minutes=1), "9.70")
    _event(service, opportunity, "TRIGGER_READY", NOW + timedelta(minutes=2), "9.90")
    _event(service, opportunity, "TRIGGERED", NOW + timedelta(minutes=3), "10.00")
    _event(service, opportunity, "ENTRY_DECISION", NOW + timedelta(minutes=4), "10.30")
    diagnostic = service.diagnose_opportunity(opportunity)
    assert diagnostic["root_diagnostic"] == "SAW_EARLY_BUT_WAITED"
    assert diagnostic["trigger_ready_at"] is not None
    assert diagnostic["entry_decision_price"] == "10.30"
    assert diagnostic["recognition_to_trigger_ready_pct"] == str((D("9.90") / D("9.50") - 1) * 100)
    assert diagnostic["trigger_to_decision_pct"] == str((D("10.30") / D("10.00") - 1) * 100)
    service.close()


def test_late_recognition_is_not_classified_as_waiting(tmp_path: Path):
    service = HistoricalDecisionIntelligence(journal_path=tmp_path / "journal.sqlite3")
    service.start("PAPER")
    opportunity = "late-opportunity"
    _event(service, opportunity, "FIRST_RECOGNIZED", NOW, "10.30", source="LEGACY_WARRIOR")
    _event(service, opportunity, "TRIGGERED", NOW + timedelta(minutes=1), "10.00", source="LEGACY_WARRIOR")
    _event(service, opportunity, "ENTRY_DECISION", NOW + timedelta(minutes=2), "10.35", source="LEGACY_WARRIOR")
    diagnostic = service.diagnose_opportunity(opportunity)
    assert diagnostic["root_diagnostic"] == "RECOGNIZED_LATE"
    service.close()


def test_source_timing_and_order_fill_linkage_survive_restart(tmp_path: Path):
    journal = tmp_path / "journal.sqlite3"
    service = HistoricalDecisionIntelligence(journal_path=journal)
    service.start("PAPER")
    opportunity = "source-opportunity"
    _event(service, opportunity, "FIRST_RECOGNIZED", NOW, "9.60", source="TAXONOMY", strategy="FIRST_PULLBACK")
    _event(service, opportunity, "FIRST_RECOGNIZED", NOW + timedelta(minutes=1), "9.90", source="LEGACY_WARRIOR", strategy="HIGH_OF_DAY_BREAKOUT")
    _event(service, opportunity, "ENTRY_DECISION", NOW + timedelta(minutes=2), "10.05", source="BOTH")
    _event(service, opportunity, "ORDER_SUBMITTED", NOW + timedelta(minutes=2), "10.05", order_id="order-1", quantity=D("10"))
    _event(service, opportunity, "FILLED", NOW + timedelta(minutes=3), "10.06", order_id="order-1", fill_id="fill-1", quantity=D("10"))
    service.close()
    restarted = HistoricalDecisionIntelligence(journal_path=journal)
    restarted.start("PAPER")
    diagnostic = restarted.diagnose_opportunity(opportunity)
    assert diagnostic["legacy_first_seen"] is not None
    assert diagnostic["taxonomy_first_seen"] is not None
    assert diagnostic["recognition_leader"] == "TAXONOMY"
    assert diagnostic["price_advantage_percent"] == str((D("9.60") / D("9.90") - 1) * 100)
    assert {row["event_type"] for row in restarted.timeline(opportunity)} == {
        "FIRST_RECOGNIZED", "ENTRY_DECISION", "ORDER_SUBMITTED", "FILLED",
    }
    restarted.close()


def test_paper_event_adapter_links_existing_order_and_fill(tmp_path: Path):
    service = HistoricalDecisionIntelligence(journal_path=tmp_path / "journal.sqlite3")
    service.start("PAPER")
    opportunity = "paper-event-opportunity"
    _event(service, opportunity, "FIRST_RECOGNIZED", NOW, "9.60")
    request = SimpleNamespace(
        strategy_lifecycle_id="lifecycle-1", metadata={"opportunity_id": opportunity},
        strategy_id="HIGH_OF_DAY_BREAKOUT", limit_price=D("10.05"),
    )
    order = SimpleNamespace(order_id="order-1", request=request)
    service.observe_paper_event(SimpleNamespace(
        event_type="ORDER_SUBMITTED", timestamp=NOW + timedelta(minutes=1),
        symbol="XYZ", order=order, fill=None,
    ))
    fill = SimpleNamespace(request_id="fill-1", fill_price=D("10.06"), quantity=D("10"))
    service.observe_paper_event(SimpleNamespace(
        event_type="ORDER_FILLED", timestamp=NOW + timedelta(minutes=2),
        symbol="XYZ", order=order, fill=fill,
    ))
    assert {row["event_type"] for row in service.timeline(opportunity)} >= {
        "ORDER_SUBMITTED", "FILLED",
    }
    service.close()


def test_trigger_ready_is_consumed_only_from_explicit_detector_armed_state(tmp_path: Path):
    service = HistoricalDecisionIntelligence(journal_path=tmp_path / "journal.sqlite3")
    service.start("PAPER")
    service._discover_memberships = lambda value: (
        ("HIGH_OF_DAY_BREAKOUT",), "runtime-anchor", "TRIGGER_ARMED",
    )
    result = service.evaluate(
        value=_value("9.95"), candidate=_candidate(SetupState.FORMING, "9.95"),
        taxonomy_candidate=object(),
    )
    assert result.setup_stage == "TRIGGER_READY"
    assert result.setup_state == "TRIGGER_ARMED"
    service.close()


def test_generic_strengthening_never_becomes_trigger_ready(tmp_path: Path):
    service = HistoricalDecisionIntelligence(journal_path=tmp_path / "journal.sqlite3")
    service.start("PAPER")
    service._discover_memberships = lambda value: (
        ("HIGH_OF_DAY_BREAKOUT",), "runtime-anchor", "STRENGTHENING",
    )
    result = service.evaluate(
        value=_value("9.95"), candidate=_candidate(SetupState.FORMING, "9.95"),
        taxonomy_candidate=object(),
    )
    assert result.setup_state == "FORMING"
    assert result.setup_stage != "TRIGGER_READY"
    service.close()


def test_real_breakout_detector_does_not_rearm_after_same_anchor_crossing():
    """The lifecycle latch is applied to actual default-registry output."""
    engine = MultiStrategyDiscoveryEngine(default_registry())
    value = _value("9.95")
    context, _ = _discovery_context(value)
    first = engine.observe(context)
    assert any(
        row.strategy_id == "HIGH_OF_DAY_BREAKOUT" and row.state.value == "TRIGGER_ARMED"
        for row in first.detections
    )

    crossing_bar = replace(
        context.completed_bars[-1], completed_at=context.completed_bars[-1].completed_at.replace(
            minute=context.completed_bars[-1].completed_at.minute + 1,
        ), open=D("10.05"), high=D("10.30"), low=D("10.00"), close=D("10.20"),
    )
    crossed = replace(
        context, decision_cutoff=crossing_bar.completed_at,
        completed_bars=(*context.completed_bars[:-1], crossing_bar),
    )
    second = engine.observe(crossed)
    assert any(
        row.strategy_id == "HIGH_OF_DAY_BREAKOUT" and row.state.value == "DETECTED"
        for row in second.detections
    )

    retrace_bar = replace(
        crossing_bar, completed_at=crossing_bar.completed_at.replace(
            minute=crossing_bar.completed_at.minute + 1,
        ), open=D("9.95"), high=D("10.00"), low=D("9.80"), close=D("9.90"),
    )
    retraced = replace(
        crossed, decision_cutoff=retrace_bar.completed_at,
        completed_bars=(*crossed.completed_bars[:-1], retrace_bar),
    )
    third = engine.observe(retraced)
    assert not any(
        row.strategy_id == "HIGH_OF_DAY_BREAKOUT" and row.state.value == "TRIGGER_ARMED"
        for row in third.lifecycle_detections
    )


def test_same_economic_episode_keeps_anchor_across_decision_bucket_change():
    value = _value("9.95")
    context, _ = _discovery_context(value)
    original = structural_anchor(context, build_impulse(context))
    later_bucket = replace(context, decision_cutoff=context.decision_cutoff + timedelta(minutes=16))
    assert structural_anchor(later_bucket, build_impulse(later_bucket)) == original


def test_triggered_anchor_is_durable_and_restored_before_discovery(tmp_path: Path):
    journal = tmp_path / "journal.sqlite3"
    first = HistoricalDecisionIntelligence(journal_path=journal)
    first.start("PAPER")
    value = _value("9.95")
    context, _ = _discovery_context(value)
    crossing_bar = replace(
        context.completed_bars[-1], completed_at=context.completed_bars[-1].completed_at + timedelta(minutes=1),
        open=D("10.05"), high=D("10.30"), low=D("10.00"), close=D("10.20"),
    )
    crossed = replace(context, decision_cutoff=crossing_bar.completed_at,
                      completed_bars=(*context.completed_bars[:-1], crossing_bar))
    crossing = first._discovery.observe(crossed)
    anchor = next(row.opportunity_anchor for row in crossing.detections
                   if row.strategy_id == "HIGH_OF_DAY_BREAKOUT" and row.state.value == "DETECTED")
    _event(first, "durable-opportunity", "TRIGGERED", crossing_bar.completed_at, "10.20",
           payload={"structural_anchor": anchor})
    first.close()

    restarted = HistoricalDecisionIntelligence(journal_path=journal)
    restarted.start("PAPER")
    assert anchor in restarted._discovery._triggered_anchors
    retrace_bar = replace(
        crossing_bar, completed_at=crossing_bar.completed_at + timedelta(minutes=16),
        open=D("9.95"), high=D("10.00"), low=D("9.80"), close=D("9.90"),
    )
    retraced = replace(crossed, decision_cutoff=retrace_bar.completed_at,
                       completed_bars=(*crossed.completed_bars[:-1], retrace_bar))
    batch = restarted._discovery.observe(retraced)
    assert not any(row.opportunity_anchor == anchor and row.state.value == "TRIGGER_ARMED"
                   for row in batch.lifecycle_detections)
    restarted.close()


def test_triggered_anchor_retention_is_bounded():
    engine = MultiStrategyDiscoveryEngine(default_registry(), maximum_opportunities=3)
    engine.restore_triggered_anchors(("a", "b", "c", "d", "e"))
    metrics = engine.memory_metrics()
    assert metrics["triggered_anchor_count"] == 3


def test_trigger_latch_survives_recomputed_anchor_after_prior_trigger():
    engine = MultiStrategyDiscoveryEngine(default_registry())
    value = _value("9.95")
    context, _ = _discovery_context(value)
    engine.observe(context)
    crossing = replace(
        context, decision_cutoff=context.decision_cutoff.replace(minute=46),
        completed_bars=(*context.completed_bars[:-1], replace(
            context.completed_bars[-1], completed_at=context.decision_cutoff.replace(minute=46),
            open=D("10.05"), high=D("10.30"), low=D("10"), close=D("10.20"),
        )),
    )
    engine.observe(crossing)
    shifted_cutoff = crossing.decision_cutoff + timedelta(minutes=21)
    shifted_bars = tuple(replace(bar, completed_at=bar.completed_at + timedelta(minutes=20))
                         for bar in crossing.completed_bars)
    shifted_retrace = replace(
        shifted_bars[-1], completed_at=shifted_cutoff,
        open=D("9.95"), high=D("10.00"), low=D("9.80"), close=D("9.90"),
    )
    new_context = replace(
        crossing, decision_cutoff=shifted_cutoff,
        completed_bars=(*shifted_bars, shifted_retrace),
    )
    batch = engine.observe(new_context)
    assert any(row.state.value == "DETECTED" for row in batch.detections)
