from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from threading import Event
from time import monotonic, perf_counter

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.performance_diagnostics import PerformanceDiagnostics
from app.strategies.warrior_momentum.desktop_sidecar import CompositeMarketEventObserver


def _event(sequence: int = 1, symbol: str = "GNPX") -> MarketEvent:
    return MarketEvent(
        sequence,
        datetime(2026, 9, 23, 15, 0, tzinfo=UTC),
        symbol,
        "test",
        MarketEventType.QUOTE,
        QuotePayload(Decimal("4.07"), Decimal("4.16"), Decimal("100"), Decimal("100")),
    )


class _Warrior:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.events: list[MarketEvent] = []

    def start(self, _environment: str | None = None) -> None:
        return None

    def stop(self) -> None:
        self.release.set()

    def __call__(self, event: MarketEvent) -> None:
        self.entered.set()
        self.release.wait(2.0)
        self.events.append(event)


def test_slow_warrior_observation_does_not_block_strict_primary() -> None:
    primary: list[MarketEvent] = []
    warrior = _Warrior()
    observer = CompositeMarketEventObserver(
        primary.append, warrior, async_warrior_observation=True,
    )
    observer.start("PAPER")
    started = monotonic()
    observer(_event())
    elapsed = monotonic() - started

    assert primary == [_event()]
    assert elapsed < 0.25
    assert warrior.entered.wait(1.0)
    warrior.release.set()
    observer.stop()


def test_strict_primary_latency_remains_bounded_under_observational_pressure() -> None:
    primary: list[float] = []
    warrior = _Warrior()
    observer = CompositeMarketEventObserver(
        lambda event: primary.append(perf_counter()),
        warrior, async_warrior_observation=True, projection_capacity=128,
    )
    observer.start("PAPER")
    started = perf_counter()
    for sequence in range(100):
        before = perf_counter()
        observer(_event(sequence + 1, f"S{sequence:03d}"))
        primary[-1] = perf_counter() - before
    warrior.release.set()
    observer.stop()
    ordered = sorted(primary)
    p99 = ordered[int(len(ordered) * 0.99) - 1]
    assert p99 < 0.05
    assert perf_counter() - started < 1.0


def test_async_warrior_handoff_is_latest_state_per_symbol_and_bounded() -> None:
    warrior = _Warrior()
    observer = CompositeMarketEventObserver(
        None, warrior, async_warrior_observation=True, projection_capacity=2,
    )
    observer.start("PAPER")
    observer(_event(1, "GNPX"))
    observer(_event(2, "GNPX"))
    observer(_event(3, "DCOY"))
    assert observer.projection_metrics()["warrior"]["high_water"] <= 2
    assert warrior.entered.wait(1.0)
    warrior.release.set()
    observer.stop()
    assert warrior.events
    assert {item.symbol for item in warrior.events} <= {"GNPX", "DCOY"}


def test_entry_funnel_metrics_are_bounded_and_sanitized() -> None:
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_entry_funnel(
        "GNPX", "EXECUTION_QUOTE_REJECTED", outcome="REJECTED",
        reason="STALE_OR_MISMATCHED", exception="secret message",
        response={"token": "must-not-appear"},
    )
    metrics = diagnostics.entry_conversion_metrics()
    assert metrics["funnel_counts"]["EXECUTION_QUOTE_REJECTED"] == 1
    record = metrics["funnel_records"][0]
    assert record["symbol"] == "GNPX"
    assert "exception" not in record
    assert "response" not in record
