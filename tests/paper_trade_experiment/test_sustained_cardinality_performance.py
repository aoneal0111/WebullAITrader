from __future__ import annotations

import os
import sqlite3
import ctypes
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from time import perf_counter, sleep
from threading import Event, Thread

import pytest

from app.paper_trade_experiment import (
    PaperTradeExperimentJournal,
    PaperTradeExperimentWorker,
)
from app.strategies.warrior_momentum import (
    ForwardCaptureStore,
    build_daily_report,
)
from tests.paper_trade_experiment.test_incremental_horizon_engine import (
    T0,
    decision,
)


RUN_SCALING = os.environ.get("ATLAS_RUN_RESEARCH_SCALING") == "1"
COUNTS = (10_000, 50_000, 100_000, 250_000, 500_000)
SYMBOL_COUNT = 12


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _working_set() -> tuple[int, int]:
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    )
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.WorkingSetSize, counters.PeakWorkingSetSize


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = round((len(values) - 1) * fraction)
    return sorted(values)[index]


def _run_workload(path, market_observations: int) -> dict[str, float | int]:
    symbols = tuple(f"S{index:02d}" for index in range(SYMBOL_COUNT))
    bases = {symbol: decision(symbol, T0, "5.00") for symbol in symbols}
    last_trade_at = {symbol: T0 for symbol in symbols}
    last_price = {symbol: Decimal("5.00") for symbol in symbols}
    worker = PaperTradeExperimentWorker(path, execution_environment="TEST")
    source_span = timedelta(hours=3)
    starting_memory, _starting_peak = _working_set()
    started = perf_counter()
    segment_started = started
    segment_rates: list[float] = []
    segment_count = market_observations // 3
    for index in range(market_observations):
        symbol = symbols[index % SYMBOL_COUNT]
        occurrence = index // SYMBOL_COUNT
        at = T0 + source_span * index / max(1, market_observations - 1)
        # A high-volume trade stream with an explicit 1% quote/duplicate mix
        # exercises the worst durable-observation rate while retaining the
        # duplicate suppression path present in production.
        is_trade = occurrence % 100 != 99
        if is_trade:
            last_trade_at[symbol] = at
            last_price[symbol] = Decimal("5") + Decimal(
                ((occurrence * 17 + index) % 201) - 100
            ) / Decimal("1000")
        item = replace(
            bases[symbol],
            timestamp=at,
            observed_at=at + timedelta(milliseconds=2),
            price=last_price[symbol],
            bid=last_price[symbol] - Decimal("0.01"),
            ask=last_price[symbol] + Decimal("0.01"),
            last_price_timestamp=last_trade_at[symbol],
            quote_timestamp=at,
            last_price_received_timestamp=(
                last_trade_at[symbol] + timedelta(milliseconds=1)
            ),
            quote_received_timestamp=at + timedelta(milliseconds=1),
            source_event_identity=f"SIM:{index}:{'TRADE' if is_trade else 'QUOTE'}",
            source_event_type="TRADE" if is_trade else "QUOTE",
        )
        if is_trade:
            assert worker.observe_price(
                symbol, last_trade_at[symbol], last_price[symbol]
            )
        assert worker.submit(item)
        while worker.metrics().queue_depth > 2048:
            sleep(0.0005)
        if index + 1 in {
            segment_count,
            segment_count * 2,
            market_observations,
        }:
            segment_finished = perf_counter()
            observations_in_segment = (
                segment_count if index + 1 < market_observations else
                market_observations - segment_count * 2
            )
            segment_rates.append(
                observations_in_segment / (segment_finished - segment_started)
            )
            segment_started = segment_finished
    submitted_at = perf_counter()
    assert worker.close(timeout_seconds=300)
    finished = perf_counter()
    ending_memory, peak_memory = _working_set()
    metrics = worker.metrics()
    journal = PaperTradeExperimentJournal(path)
    snapshot = journal.completeness_snapshot()
    records = journal.records()
    journal.close()
    connection = sqlite3.connect(path)
    lag_values = [
        row[0] for row in connection.execute(
            """SELECT MAX(0,
                      (julianday(started_at)-julianday(enqueued_at))*86400000.0)
               FROM research_work_items WHERE state='COMPLETED'"""
        ).fetchall()
    ]
    duplicate_identities = connection.execute(
        """SELECT COUNT(*) FROM (
           SELECT work_id FROM research_work_items
           GROUP BY work_id HAVING COUNT(*)>1)"""
    ).fetchone()[0]
    connection.close()
    result = {
        "market_observations": market_observations,
        "producer_rate": market_observations / (submitted_at - started),
        "service_rate": metrics.completed / (finished - started),
        "queue_hwm": metrics.queue_high_water,
        "queue_final": metrics.queue_depth,
        "durable_accepted": snapshot["items_accepted"],
        "durable_outstanding": snapshot["durable_outstanding"],
        "candidate_count": len(records),
        "active_candidates": snapshot["active_candidate_count"],
        "complete_candidates": snapshot["complete_candidate_count"],
        "lag_p50_ms": median(lag_values),
        "lag_p90_ms": _percentile(lag_values, 0.90),
        "lag_p95_ms": _percentile(lag_values, 0.95),
        "lag_p99_ms": _percentile(lag_values, 0.99),
        "lag_max_ms": max(lag_values, default=0.0),
        "rejections": metrics.rejected,
        "failures": metrics.failures,
        "duplicate_identities": duplicate_identities,
        "suppressed_duplicates": metrics.suppressed_duplicates,
        "peak_memory_bytes": peak_memory,
        "memory_growth_bytes": ending_memory - starting_memory,
        "segment_market_rates": tuple(segment_rates),
        "maximum_expected_active_candidates": SYMBOL_COUNT,
    }
    print(f"ATLAS_RESEARCH_SCALING {result}")
    return result


@pytest.mark.skipif(not RUN_SCALING, reason="explicit sustained scaling validation")
@pytest.mark.parametrize("market_observations", COUNTS)
def test_sustained_three_hour_cardinality_and_throughput(
    tmp_path, market_observations,
) -> None:
    result = _run_workload(
        tmp_path / f"research-{market_observations}.sqlite3",
        market_observations,
    )
    assert result["service_rate"] >= 171
    assert result["queue_hwm"] < 8192
    assert result["queue_final"] == 0
    assert result["durable_outstanding"] == 0
    assert result["rejections"] == 0
    assert result["failures"] == 0
    assert result["duplicate_identities"] == 0
    assert result["candidate_count"] == SYMBOL_COUNT
    assert result["active_candidates"] == 0
    assert result["complete_candidates"] == SYMBOL_COUNT


@pytest.mark.skipif(not RUN_SCALING, reason="explicit report-contention validation")
def test_report_full_history_contention_retains_research_headroom(tmp_path) -> None:
    report_path = tmp_path / "forward-report.sqlite3"
    store = ForwardCaptureStore(report_path)
    connection = sqlite3.connect(report_path)
    with connection:
        connection.executemany(
            """INSERT INTO capture_records(
               record_id,schema_version,record_type,symbol,timestamp,payload_json
               ) VALUES(?,1,'DISCOVERY',?,?,?)""",
            (
                (f"report-{index}", f"S{index % SYMBOL_COUNT:02d}",
                 T0.isoformat(), "{}")
                for index in range(100_000)
            ),
        )
    connection.close()

    idle = _run_workload(tmp_path / "research-idle.sqlite3", 10_000)
    stop = Event()
    entered = Event()
    builds = []

    def report_load() -> None:
        while not stop.is_set():
            entered.set()
            build_daily_report(store, date(2026, 8, 31))
            builds.append(1)

    thread = Thread(target=report_load, name="representative-report-load")
    thread.start()
    assert entered.wait(5)
    loaded = _run_workload(tmp_path / "research-loaded.sqlite3", 10_000)
    stop.set()
    thread.join(30)
    assert not thread.is_alive()
    ratio = loaded["service_rate"] / idle["service_rate"]
    print(
        "ATLAS_REPORT_CONTENTION "
        f"{{'idle_rate': {idle['service_rate']}, "
        f"'loaded_rate': {loaded['service_rate']}, "
        f"'loaded_to_idle_ratio': {ratio}, 'report_builds': {len(builds)}}}"
    )
    assert loaded["service_rate"] >= 171
    assert loaded["rejections"] == 0
    assert loaded["durable_outstanding"] == 0
