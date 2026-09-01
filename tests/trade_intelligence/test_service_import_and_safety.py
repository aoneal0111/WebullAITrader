from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import tracemalloc
from threading import Event
from time import perf_counter, sleep

from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.importer import import_external_snapshot_rows
from app.trade_intelligence.models import (
    ActualPaperExecutionOutcome, PriceBar,
    experience_payload,
)
from app.trade_intelligence.service import TradeIntelligenceService
from tests.trade_intelligence.conftest import T0, make_experience


def _wait(predicate, timeout=10):
    deadline = perf_counter() + timeout
    while perf_counter() < deadline:
        if predicate():
            return True
        sleep(0.005)
    return False


def _bars(symbol="ABCD", count=30):
    return tuple(
        PriceBar(symbol, T0 + timedelta(minutes=index), Decimal("10"), Decimal("10.1"), Decimal("9.8"), Decimal("10"), Decimal("1000"))
        for index in range(count)
    )


def test_orderly_shutdown_loses_no_accepted_work_and_restart_recovers(tmp_path):
    path = tmp_path / "memory.sqlite3"
    service = TradeIntelligenceService(path, capacity=128)
    assert service.submit_experience(make_experience())
    assert all(service.observe_completed_bar(item) for item in _bars())
    assert service.close(timeout_seconds=20)
    metrics = service.metrics()
    assert metrics.accepted == metrics.completed
    assert metrics.outstanding == metrics.failed == 0
    store = ExperienceStore(path)
    assert store.count() == 1
    assert len(store.outcomes(make_experience().experience_id)) == 6
    assert store.accounting()["outstanding"] == 0
    assert store.accounting()["accepted"] == metrics.accepted
    restarted = TradeIntelligenceService(path)
    assert restarted.close(timeout_seconds=10)
    assert ExperienceStore(path).count() == 1


def test_checkpointed_payload_replays_idempotently_after_restart(tmp_path):
    path = tmp_path / "memory.sqlite3"
    exp = make_experience()
    store = ExperienceStore(path)
    assert store.checkpoint_work(
        exp.experience_id, "EXPERIENCE", T0, experience_payload(exp),
    )
    store.start_work(exp.experience_id, T0)
    service = TradeIntelligenceService(path)
    assert _wait(lambda: ExperienceStore(path).count() == 1)
    assert service.close(timeout_seconds=10)
    assert service.metrics().accepted == service.metrics().completed == 1
    assert ExperienceStore(path).accounting()["outstanding"] == 0


def test_duplicate_observations_are_suppressed(tmp_path):
    service = TradeIntelligenceService(tmp_path / "memory.sqlite3")
    exp = make_experience()
    assert service.submit_experience(exp)
    assert service.submit_experience(exp)
    assert service.observe_completed_bar(_bars()[0])
    assert service.observe_completed_bar(_bars()[0])
    assert service.close(timeout_seconds=10)
    assert service.metrics().suppressed_duplicate == 2
    assert ExperienceStore(tmp_path / "memory.sqlite3").accounting()["suppressed_duplicate"] == 2


def test_bounded_pressure_rejects_then_recovers_without_permanent_disable(tmp_path):
    entered = Event()
    release = Event()

    class BlockingStore(ExperienceStore):
        def put_experience(self, value):
            entered.set()
            release.wait(5)
            return super().put_experience(value)

    service = TradeIntelligenceService(tmp_path / "memory.sqlite3", capacity=1, store_factory=BlockingStore)
    assert service.submit_experience(make_experience(episode="one"))
    assert entered.wait(5)
    assert service.submit_experience(make_experience(episode="two"))
    assert not service.submit_experience(make_experience(episode="three"))
    release.set()
    assert _wait(lambda: service.metrics().queue_depth == 0)
    assert service.submit_experience(make_experience(episode="four"))
    assert service.close(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.pressure_episodes == 1 and metrics.pressure_recoveries == 1
    assert metrics.rejected == 1 and metrics.failed == 0
    assert ExperienceStore(tmp_path / "memory.sqlite3").count() == 3


def test_external_snapshot_import_is_reproducible_and_deduplicated(tmp_path):
    snapshot = tmp_path / "external-snapshot.fixture"
    snapshot.write_text("fixture", encoding="utf-8")
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    rows = ({"source_record_identity": "row-1"},)
    decoder = lambda row: make_experience(episode=str(row["source_record_identity"]))
    assert import_external_snapshot_rows(
        store, snapshot, rows, decoder,
        source_schema_version="fixture-v1", import_version="atlas-import-v1",
    ) == (1, 0)
    assert import_external_snapshot_rows(
        store, snapshot, rows, decoder,
        source_schema_version="fixture-v1", import_version="atlas-import-v1",
    ) == (0, 1)
    assert snapshot.read_text(encoding="utf-8") == "fixture"


def test_actual_paper_records_remain_separate_from_autonomous_memory(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    exp = make_experience()
    store.put_experience(exp)
    assert store.put_actual_paper_outcome(ActualPaperExecutionOutcome(
        exp.experience_id, "paper-fill-1", Decimal("10"), Decimal("10.5"),
        100, Decimal("50"), T0, T0 + timedelta(minutes=5),
    ))
    # Actual execution facts do not mutate autonomous experience content.
    assert store.count() == 1 and store.get_experience(exp.experience_id) == exp


def test_package_has_no_execution_or_broker_dependency():
    root = __import__("pathlib").Path(__file__).parents[2] / "app" / "trade_intelligence"
    forbidden = (
        "app.live_execution", "app.paper_gateway", "app.broker",
        "app.authorization", "app.execution", "app.order",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert not any(item in source for item in forbidden)
    assert "submit_order" not in source and "authorize_order" not in source


def test_three_hour_tick_cardinality_and_burst_headroom(tmp_path):
    """Synthetic 3h: 12 symbols, 324k ticks, 24 logical opportunities.

    Tick generation is intentionally producer-local: only meaningful episode
    transitions cross the research queue, proving count scales with episodes.
    The burst assertion covers >100x the observed 27 events/s admission demand.
    """
    tracemalloc.start()
    service = TradeIntelligenceService(tmp_path / "memory.sqlite3", capacity=4096)
    symbols = tuple(f"S{index:02d}" for index in range(12))
    ticks = 324_000
    started = perf_counter()
    episode_values = []
    for index in range(ticks):
        symbol = symbols[index % len(symbols)]
        # One episode at open and one halfway through; ordinary ticks are
        # observations within the episode and create no experience work.
        if index < len(symbols):
            item = make_experience(symbol, "open")
            episode_values.append(item)
            assert service.submit_experience(item)
        elif index == ticks // 2 + int(symbol[1:]):
            item = make_experience(symbol, "continuation", at=T0 + timedelta(minutes=90))
            episode_values.append(item)
            assert service.submit_experience(item)
    producer_elapsed = perf_counter() - started
    # Completed 1-minute data for every symbol across the full three-hour
    # source span. These are bounded research observations, not raw ticks.
    for minute in range(180):
        for symbol in symbols:
            assert service.observe_completed_bar(PriceBar(
                symbol, T0 + timedelta(minutes=minute), Decimal("10"),
                Decimal("10.2"), Decimal("9.8"), Decimal("10.05"),
                Decimal("1000"),
            ))
    submitted_at = perf_counter()
    assert service.close(timeout_seconds=30)
    finished_at = perf_counter()
    _current_memory, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rate = ticks / producer_elapsed
    service_rate = service.metrics().completed / (finished_at - started)
    metrics = service.metrics()
    assert rate > 2700
    assert service_rate > 54  # 2x observed 27/s including FULL SQLite durability.
    assert ExperienceStore(tmp_path / "memory.sqlite3").count() == 24
    assert metrics.rejected == metrics.failed == metrics.outstanding == 0
    assert metrics.queue_depth == 0 and metrics.queue_high_water <= 4096
    assert peak_memory < 64 * 1024 * 1024
    print({
        "synthetic_hours": 3, "symbols": 12, "raw_ticks": ticks,
        "logical_experiences": 24, "completed_bars": 2160,
        "raw_loop_rate_per_second": round(rate, 1),
        "durable_service_rate_per_second": round(service_rate, 1),
        "queue_high_water": metrics.queue_high_water,
        "peak_traced_memory_bytes": peak_memory,
        "rejected": metrics.rejected, "failed": metrics.failed,
    })
