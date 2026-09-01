from __future__ import annotations

import ctypes
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import os
from pathlib import Path
from time import perf_counter

import pytest

from app.trade_intelligence.analogs import AnalogQuery, HistoricalAnalogEngine
from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.models import (
    HORIZONS_MINUTES, AtlasDecision, HorizonOutcome, OutcomeKind, OutcomeStatus,
    experience_payload,
)
from app.trade_intelligence.reporting import ExperienceReporter
from app.trade_intelligence.service import TradeIntelligenceService
from tests.trade_intelligence.conftest import T0, make_experience

RUN_SCALING = os.environ.get("ATLAS_RUN_TRADE_INTELLIGENCE_SCALING") == "1"
CHECKPOINTS = (10_000, 50_000, 100_000)
BATCH_SIZE = 1_000


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _working_set() -> int:
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(_ProcessMemoryCounters), ctypes.c_ulong,
    )
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.WorkingSetSize)


def _experience(index: int):
    at = T0 + timedelta(milliseconds=index)
    exp = make_experience(
        f"S{index % 64:02d}", f"logical-{index}",
        decision=AtlasDecision.REJECTED,
        blockers=(("NO_CATALYST", "SPREAD_WIDE", "RVOL_LOW")[index % 3],),
        at=at,
    )
    price = Decimal("5") + Decimal(index % 1500) / Decimal("100")
    snapshot = replace(
        exp.snapshot, last_price=price, reference_price=price,
        trigger_price=price, structural_stop=price - Decimal("0.25"),
        risk_per_share=Decimal("0.25"), percentage_change=Decimal(5 + index % 60),
        relative_volume=Decimal("1") + Decimal(index % 120) / Decimal("10"),
        scanner_rank=index % 100 + 1,
    )
    return replace(exp, snapshot=snapshot)


def _outcomes(exp):
    for horizon in HORIZONS_MINUTES:
        reached_2r = horizon >= 2
        reached_3r = horizon >= 5
        yield HorizonOutcome(
            exp.experience_id, horizon,
            exp.snapshot.decision_timestamp + timedelta(minutes=horizon),
            OutcomeStatus.COMPLETE,
            future_price=exp.snapshot.reference_price + Decimal("0.50"),
            return_percent=Decimal("5"), mfe=Decimal("0.75"),
            mae=Decimal("-0.10"), mfe_r=Decimal("3"), mae_r=Decimal("-0.4"),
            reached_1r=True, reached_2r=reached_2r, reached_3r=reached_3r,
            stop_reached=False, time_to_1r_seconds=60,
            time_to_2r_seconds=120 if reached_2r else None,
            time_to_3r_seconds=300 if reached_3r else None,
            first_plan_event="1R", outcome_as_of=exp.snapshot.decision_timestamp + timedelta(minutes=horizon),
            plan_outcome_kind=OutcomeKind.HYPOTHETICAL_EXECUTION,
        )


def _worker_probe(path: Path, count: int = 2_000) -> dict[str, object]:
    service = TradeIntelligenceService(path, capacity=count + 1)
    started = perf_counter()
    for index in range(count):
        assert service.submit_experience(_experience(index))
    submitted = perf_counter()
    assert service.close(timeout_seconds=180)
    finished = perf_counter()
    metrics = service.metrics()
    return {
        "count": count,
        "producer_rate": count / (submitted - started),
        "service_rate": count / (finished - started),
        "queue_hwm": metrics.queue_high_water,
        "queue_final": metrics.queue_depth,
        "rejected": metrics.rejected, "failed": metrics.failed,
    }


@pytest.mark.skipif(not RUN_SCALING, reason="explicit logical-experience scaling validation")
def test_logical_experience_history_scaling(tmp_path) -> None:
    path = tmp_path / "logical-history.sqlite3"
    store = ExperienceStore(path)
    starting_memory = _working_set()
    results = []
    previous = 0
    last_exp = None
    for checkpoint in CHECKPOINTS:
        experience_seconds = outcome_seconds = 0.0
        segment_started = perf_counter()
        for batch_start in range(previous, checkpoint, BATCH_SIZE):
            batch_end = min(checkpoint, batch_start + BATCH_SIZE)
            experiences = tuple(_experience(index) for index in range(batch_start, batch_end))
            last_exp = experiences[-1]
            started = perf_counter()
            inserted, duplicates = store.put_experiences(experiences)
            experience_seconds += perf_counter() - started
            assert inserted == len(experiences) and duplicates == 0
            started = perf_counter()
            outcome_values = tuple(outcome for exp in experiences for outcome in _outcomes(exp))
            outcomes_inserted, outcome_duplicates = store.put_outcomes(outcome_values)
            outcome_seconds += perf_counter() - started
            assert outcomes_inserted == len(experiences) * 6 and outcome_duplicates == 0
        segment_seconds = perf_counter() - segment_started
        database_bytes = store.checkpoint_and_size_bytes()
        analog_started = perf_counter()
        analog = HistoricalAnalogEngine(store).query(AnalogQuery(
            last_exp, last_exp.snapshot.decision_timestamp + timedelta(seconds=1),
            minimum_sample_size=20, limit=200,
        ))
        analog_seconds = perf_counter() - analog_started
        report_started = perf_counter()
        report = ExperienceReporter(store).summary()
        report_seconds = perf_counter() - report_started
        results.append({
            "checkpoint": checkpoint,
            "segment_count": checkpoint - previous,
            "experience_insert_rate": (checkpoint - previous) / experience_seconds,
            "outcome_insert_rate": ((checkpoint - previous) * 6) / outcome_seconds,
            "combined_segment_rate": (checkpoint - previous) / segment_seconds,
            "database_bytes": database_bytes,
            "bytes_per_experience": database_bytes / checkpoint,
            "working_set_growth_bytes": _working_set() - starting_memory,
            "analog_query_ms": analog_seconds * 1000,
            "analog_samples": analog.sample_size,
            "report_ms": report_seconds * 1000,
            "report_count": report["unique_experiences"],
        })
        previous = checkpoint
    assert last_exp is not None
    # Restart a completed 100K history and replay one checkpointed idempotent work item.
    payload = experience_payload(last_exp)
    store.checkpoint_work("restart-probe", "EXPERIENCE", T0, payload)
    restart_started = perf_counter()
    restarted = TradeIntelligenceService(path)
    assert restarted.close(timeout_seconds=180)
    restart_seconds = perf_counter() - restart_started
    worker = _worker_probe(tmp_path / "worker-probe.sqlite3")
    print("ATLAS_LOGICAL_EXPERIENCE_SCALING", {
        "checkpoints": results, "restart_seconds": restart_seconds,
        "worker_probe": worker,
    })
    assert store.count() == 100_000
    assert all(item["report_count"] == item["checkpoint"] for item in results)
    assert all(item["analog_query_ms"] < 2_000 for item in results)
    assert all(item["combined_segment_rate"] > 27 for item in results)
    segment_rates = [item["combined_segment_rate"] for item in results]
    assert min(segment_rates) / max(segment_rates) >= 0.50
    assert restart_seconds < 30
    assert worker["service_rate"] > 54
    assert worker["queue_final"] == worker["rejected"] == worker["failed"] == 0
