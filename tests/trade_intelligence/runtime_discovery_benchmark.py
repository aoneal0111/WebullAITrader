"""Deterministic Phase 2A.2 bounded-worker scaling probe (not pytest-collected)."""

from __future__ import annotations

from collections import deque
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
import tracemalloc

from app.opportunity_discovery import CompletedBar, DiscoveryContext, PositionFocusTier
from app.trade_intelligence.discovery_runtime import RuntimeDiscoveryObservation
from app.trade_intelligence.service import TradeIntelligenceService


class _MemoryStore:
    """Worker-owned persistence façade for measuring discovery rather than disk."""

    def __init__(self, path):
        self.counts = {
            "opportunities": 0, "memberships": 0, "transitions": 0,
            "correlations": 0, "thesis": 0, "add_ons": 0,
        }

    def recover_started_work(self): return 0
    def incomplete_experiences(self): return ()
    def recoverable_work(self): return ()
    def checkpoint_work(self, *args): return True
    def start_work(self, *args): return None
    def complete_work(self, *args): return None
    def record_admission_accounting(self, **kwargs): return None
    def put_discovery_opportunity(self, value): self.counts["opportunities"] += 1; return True
    def put_strategy_membership(self, value): self.counts["memberships"] += 1; return True
    def put_strategy_transition(self, value): self.counts["transitions"] += 1; return True
    def put_position_correlation(self, value): self.counts["correlations"] += 1; return True
    def put_position_thesis(self, value): self.counts["thesis"] += 1; return True
    def put_add_on_candidate(self, value): self.counts["add_ons"] += 1; return True


def main() -> None:
    started_at = datetime(2026, 9, 3, 13, 30, tzinfo=UTC)
    symbols = tuple(f"S{index:03d}" for index in range(100))
    histories = {symbol: deque(maxlen=64) for symbol in symbols}
    stores = []

    def factory(path):
        value = _MemoryStore(path)
        stores.append(value)
        return value

    tracemalloc.start()
    started = perf_counter()
    with TemporaryDirectory(prefix="atlas-phase2a2-benchmark-") as directory:
        service = TradeIntelligenceService(
            Path(directory) / "memory.sqlite3", capacity=512,
            store_factory=factory,
        )
        accepted = rejected = 0
        for minute in range(120):
            for symbol_index, symbol in enumerate(symbols):
                baseline = Decimal("5") + Decimal(symbol_index) / Decimal("100")
                cycle = Decimal(minute % 12) / Decimal("100")
                direction = Decimal("0.12") if minute % 5 else Decimal("-0.08")
                opened = baseline + cycle
                closed = opened + direction
                bar = CompletedBar(
                    symbol, started_at + timedelta(minutes=minute + 1),
                    opened, max(opened, closed) + Decimal("0.04"),
                    min(opened, closed) - Decimal("0.03"), closed,
                    Decimal(1000 + (minute % 7) * 150), "REGULAR",
                )
                histories[symbol].append(bar)
                cutoff = bar.completed_at
                observation = RuntimeDiscoveryObservation(
                    DiscoveryContext(
                        symbol, date(2026, 9, 3), "REGULAR", cutoff,
                        tuple(histories[symbol]), scanner_rank=symbol_index + 1,
                    ),
                    cutoff, PositionFocusTier.SCANNER_DISCOVERY,
                )
                if service.submit_discovery_observation(observation):
                    accepted += 1
                else:
                    rejected += 1
                while service.metrics().queue_depth > 384:
                    sleep(0.001)
        service.close(timeout_seconds=60)
        metrics = service.metrics()
        telemetry = service.discovery_telemetry()
    elapsed = perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({
        "synthetic_market_observations": 120_000,
        "completed_bars": 12_000,
        "accepted": accepted,
        "rejected": rejected,
        "completed": metrics.completed,
        "failed": metrics.failed,
        "outstanding": metrics.outstanding,
        "queue_hwm": metrics.queue_high_water,
        "queue_final": metrics.queue_depth,
        "lag_p50_ms": metrics.worker_lag_p50_ms,
        "lag_p90_ms": metrics.worker_lag_p90_ms,
        "lag_p99_ms": metrics.worker_lag_p99_ms,
        "lag_max_ms": metrics.worker_lag_max_ms,
        "cycles": telemetry.discovery_cycles,
        "detector_evaluations": telemetry.detector_evaluations,
        "raw_firings": telemetry.raw_detector_firings,
        "unique_episodes": telemetry.unique_detector_episodes,
        "normalized_opportunities": telemetry.normalized_opportunities,
        "memberships": telemetry.strategy_memberships,
        "transitions": telemetry.strategy_transitions,
        "elapsed_seconds": round(elapsed, 3),
        "cycles_per_second": round(telemetry.discovery_cycles / elapsed, 1),
        "peak_tracemalloc_bytes": peak,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
