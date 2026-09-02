from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
from time import perf_counter
import ctypes

import pytest

from app.market_data.models import MarketEvent, MarketEventType, TradePayload
from app.momentum_scanner.models import (
    CatalystStatus, CatalystType, ScannerDecision, ScannerMetrics,
)
from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.runtime import TradeIntelligenceRuntimeObserver


pytestmark = pytest.mark.skipif(
    os.getenv("ATLAS_RUN_RUNTIME_SCALING") != "1",
    reason="explicit Phase 1B runtime scaling benchmark",
)


def _decision(symbol: str, at: datetime, identity: str) -> ScannerDecision:
    return ScannerDecision(
        symbol=symbol, qualified=True, score=90,
        metrics=ScannerMetrics(Decimal("25"), Decimal("8"),
                               Decimal("1000000"), Decimal("0.2")),
        passed_rules=("price_range", "relative_volume"), failed_rules=(),
        timestamp=at, observed_at=at, price=Decimal("10"),
        current_volume=Decimal("100000"), average_30_day_volume=Decimal("12500"),
        float_shares=Decimal("4000000"), bid=Decimal("9.99"), ask=Decimal("10.01"),
        tradable=True, halted=False, catalyst=CatalystType.EARNINGS,
        catalyst_status=CatalystStatus.TRUE,
        technical_qualifies_without_catalyst=True, scanner_rank=1,
        source_event_identity=identity, source_event_type="TRADE",
        last_price_timestamp=at, quote_timestamp=at,
    )


def _event(symbol: str, at: datetime, sequence: int) -> MarketEvent:
    return MarketEvent(
        sequence=sequence, timestamp=at, symbol=symbol, source="synthetic",
        event_type=MarketEventType.TRADE,
        payload=TradePayload(Decimal("10") + Decimal(sequence % 20) / 100,
                             Decimal("100"), f"trade-{sequence}"),
    )


def _db_bytes(path: Path) -> int:
    return sum(
        item.stat().st_size for item in (
            path, Path(str(path) + "-wal"), Path(str(path) + "-shm")
        ) if item.exists()
    )


def _working_set_bytes() -> int:
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
            ("quota_peak_paged", ctypes.c_size_t), ("quota_paged", ctypes.c_size_t),
            ("quota_peak_nonpaged", ctypes.c_size_t), ("quota_nonpaged", ctypes.c_size_t),
            ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t),
        ]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong,
    )
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.working_set)


def test_three_hour_market_cardinality_and_dense_opportunities(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    runtime = TradeIntelligenceRuntimeObserver(
        enabled=True, environment="TEST", path=path, capacity=4096,
    )
    runtime.start()
    start = datetime(2026, 8, 31, 13, 30, tzinfo=UTC)
    symbols = tuple(f"S{index:02d}" for index in range(12))
    raw_events = 324_000
    scanner_values = {
        symbol: _decision(symbol, start, f"scanner:start:{symbol}")
        for symbol in symbols
    }
    memory_before = _working_set_bytes()
    wall = perf_counter()
    sequence = 0
    for second in range(10_800):
        symbol = symbols[second % len(symbols)]
        at = start + timedelta(seconds=second)
        if second % 100 == 0:
            scanner_values[symbol] = replace(
                scanner_values[symbol], scanner_rank=1 + second % 25,
                bid=Decimal("9.98") + Decimal(second % 3) / 100,
                ask=Decimal("10.01") + Decimal(second % 3) / 100,
                source_event_identity=f"scanner:{second}",
            )
        event = _event(symbol, at, second + 1)
        for _ in range(30):
            runtime.observe_scanner_decision(scanner_values[symbol])
            runtime(event)
            sequence += 1
    market_elapsed = perf_counter() - wall
    print(f"PHASE1B_MARKET_PRODUCER_DONE seconds={market_elapsed:.3f}", flush=True)
    memory_after = _working_set_bytes()
    for index, symbol in enumerate(symbols):
        runtime(_event(symbol, start + timedelta(hours=3, seconds=1), raw_events + index + 1))
    assert runtime.stop(timeout_seconds=60)
    print("PHASE1B_MARKET_DRAIN_DONE", flush=True)
    market_metrics = runtime.metrics()
    # stop releases the owned service; inspect durable state for exact counts.
    store = ExperienceStore(path)
    market_experiences = store.count()
    market_outcomes = sum(len(store.outcomes(item.experience_id)) for item in store.experiences())
    market_bytes = _db_bytes(path)

    dense_path = tmp_path / "dense.sqlite3"
    dense = TradeIntelligenceRuntimeObserver(
        enabled=True, environment="TEST", path=dense_path, capacity=4096,
    )
    dense.start()
    dense_wall = perf_counter()
    for index in range(1_000):
        at = start + timedelta(milliseconds=index)
        dense.reset_symbol("DENSE")
        dense.observe_scanner_decision(_decision("DENSE", at, f"dense:{index}"))
    print("PHASE1B_DENSE_PRODUCER_DONE", flush=True)
    for minute in range(32):
        dense(_event("DENSE", start + timedelta(minutes=minute), raw_events + 100 + minute))
    assert dense.stop(timeout_seconds=120)
    service_metrics = dense.metrics()
    dense_elapsed = perf_counter() - dense_wall
    dense_store = ExperienceStore(dense_path)
    dense_experiences = dense_store.count()
    dense_outcomes = sum(len(dense_store.outcomes(item.experience_id)) for item in dense_store.experiences())
    result = {
        "market": {
            "raw_events": raw_events, "scanner_evaluations": raw_events,
            "logical_experiences": market_experiences,
            "decision_history": sum(len(store.decision_observations(item.experience_id)) for item in store.experiences()),
            "completed_outcomes": market_outcomes,
            "experience_raw_ratio": market_experiences / raw_events,
            "producer_events_per_second": raw_events / market_elapsed,
            "producer_elapsed_seconds": market_elapsed,
            "working_set_growth_bytes": memory_after - memory_before,
            "database_bytes": market_bytes,
            "queue_high_water": market_metrics.queue_high_water,
            "final_queue": market_metrics.queue_depth,
            "accepted": market_metrics.accepted,
            "completed": market_metrics.completed,
            "worker_lag_max_ms": market_metrics.worker_lag_max_ms,
            "worker_lag_p50_ms": market_metrics.worker_lag_p50_ms,
            "worker_lag_p90_ms": market_metrics.worker_lag_p90_ms,
            "worker_lag_p99_ms": market_metrics.worker_lag_p99_ms,
            "rejections": market_metrics.rejected,
            "failures": market_metrics.failed,
            "outstanding": market_metrics.outstanding,
        },
        "dense": {
            "logical_experiences": dense_experiences,
            "completed_outcomes": dense_outcomes,
            "elapsed_seconds": dense_elapsed,
            "database_bytes": _db_bytes(dense_path),
            "queue_high_water": service_metrics.queue_high_water,
            "accepted": service_metrics.accepted,
            "completed": service_metrics.completed,
            "outstanding": service_metrics.outstanding,
            "worker_lag_max_ms": service_metrics.worker_lag_max_ms,
            "worker_lag_p50_ms": service_metrics.worker_lag_p50_ms,
            "worker_lag_p90_ms": service_metrics.worker_lag_p90_ms,
            "worker_lag_p99_ms": service_metrics.worker_lag_p99_ms,
            "rejections": service_metrics.rejected,
            "failures": service_metrics.failed,
        },
    }
    print("PHASE1B_BENCHMARK=" + json.dumps(result, sort_keys=True))
    assert market_experiences == len(symbols)
    assert market_outcomes == len(symbols) * 6
    assert market_experiences / raw_events < Decimal("0.001")
    assert dense_experiences == 1_000
    assert dense_outcomes == 6_000
    assert service_metrics.rejected == 0
    assert service_metrics.failed == 0
    assert service_metrics.outstanding == 0
    assert market_metrics.rejected == market_metrics.failed == market_metrics.outstanding == 0
