from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
import sqlite3

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
    assert result.setup_stage == "NEAR_TRIGGER"
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
        ).fetchone()[0] == 2


def _event(service, opportunity, kind, at, price, source="TAXONOMY", **extra):
    service.record_event(
        opportunity_id=opportunity, event_type=kind, observed_at=at,
        symbol="XYZ", source=source, price=D(price), trigger_price=D("10"),
        **extra,
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
