from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.performance_diagnostics import PerformanceDiagnostics
from app.strategies.warrior_momentum.desktop_sidecar import CompositeMarketEventObserver


def _event() -> MarketEvent:
    return MarketEvent(
        1,
        datetime(2026, 9, 11, 8, tzinfo=UTC),
        "DBGI",
        "test",
        MarketEventType.QUOTE,
        QuotePayload(Decimal("7.20"), Decimal("7.21"), Decimal("100"), Decimal("200")),
    )


def test_component_timing_is_bounded_and_aggregated():
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_component_duration("slow-observer", 4500.0, event_type="QUOTE", symbol="DBGI", success=False)
    diagnostics.record_component_duration("slow-observer", 10.0)

    metrics = diagnostics.snapshot().component_timings["slow-observer"]
    assert metrics["calls"] == 2
    assert metrics["failures"] == 1
    assert metrics["max_ms"] == 4500.0
    assert metrics["p50_ms"] == 10.0
    assert metrics["slow_calls"] == 1


def test_composite_observer_records_each_child_without_reordering():
    calls: list[str] = []

    def primary(event):
        calls.append("paper")

    def warrior(event):
        calls.append("warrior")

    def research(event):
        calls.append("research")

    def adaptive(event):
        calls.append("adaptive")

    observer = CompositeMarketEventObserver(primary, warrior, research, adaptive)
    observer(_event())

    assert calls == ["paper", "warrior", "research", "adaptive"]


def test_startup_metrics_include_stage_durations_and_bounded_symbols():
    diagnostics = PerformanceDiagnostics()
    for stage in ("runtime_started", "broker_connect_started", "broker_connected"):
        diagnostics.record_startup_stage(stage)
    diagnostics.record_startup_symbol("dbgi")
    diagnostics.record_startup_symbol("DBGI")

    startup = diagnostics.startup_metrics()
    assert startup["unique_symbols_observed_before_feed_healthy"] == 1
    assert startup["broker_connect_ms"] is not None
