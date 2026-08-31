from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from statistics import fmean
from time import perf_counter

import pytest

from app.paper_trade_experiment import PaperTradeExperimentJournal, PaperTradeExperimentWorker
from tests.paper_trade_experiment.test_incremental_horizon_engine import T0, decision


def _seed_rows(
    journal: PaperTradeExperimentJournal,
    *,
    start: int,
    count: int,
    active: bool,
) -> None:
    connection = journal._connection
    features = json.dumps(
        {"counterfactual_reference_price": "5"},
        sort_keys=True,
        separators=(",", ":"),
    )
    labels = json.dumps(
        {} if active else {"outcome_status": "COMPLETE", "price_after_30m": "5"},
        sort_keys=True,
        separators=(",", ":"),
    )
    execution = json.dumps({"state": "NOT_EXECUTED"})
    candidate_rows = [
        (
            f"scale-{start + index}", None, "SCALE",
            (T0 - timedelta(seconds=1)).isoformat(), features, labels,
            execution, T0.isoformat(), T0.isoformat(),
        )
        for index in range(count)
    ]
    with connection:
        connection.executemany(
            "INSERT INTO experiment_candidates VALUES(?,?,?,?,?,?,?,?,?)",
            candidate_rows,
        )
        if active:
            completed = json.dumps({"completed": []}, separators=(",", ":"))
            connection.executemany(
                "INSERT INTO research_active_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f"scale-{start + index}", "SCALE",
                        (T0 - timedelta(seconds=1)).isoformat(), "5", "1m",
                        (T0 + timedelta(minutes=1)).isoformat(), completed,
                        "0", "0", None, 1, 0, 0,
                    )
                    for index in range(count)
                ],
            )


def test_complete_history_does_not_affect_observation_cost(tmp_path) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / "complete-history.sqlite3")
    _seed_rows(journal, start=0, count=25, active=True)
    prior = 0
    means = []
    for size in (100, 500, 1000, 2500, 5000, 10000, 25000, 50000):
        _seed_rows(
            journal, start=100000 + prior, count=size - prior, active=False
        )
        prior = size
        samples = []
        for _ in range(25):
            started = perf_counter()
            journal.observe_price("SCALE", T0 + timedelta(seconds=30), 5)
            samples.append((perf_counter() - started) * 1000)
        means.append(fmean(samples))
    assert max(means) < max(1.0, min(means) * 5)
    assert journal.completeness_snapshot()["active_candidate_count"] == 25


@pytest.mark.parametrize("active_count", (1, 10, 25, 50, 100, 250, 500, 1000))
def test_observation_cost_is_bounded_by_active_set(tmp_path, active_count) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / f"active-{active_count}.sqlite3")
    _seed_rows(journal, start=0, count=active_count, active=True)
    assert journal.observe_price(
        "SCALE", T0 + timedelta(seconds=30), 5
    ) == active_count
    assert journal.completeness_snapshot()["active_candidate_count"] == active_count


def test_offline_live_rate_burst_drains_with_headroom(tmp_path) -> None:
    worker = PaperTradeExperimentWorker(
        tmp_path / "burst.sqlite3", execution_environment="TEST", capacity=8192
    )
    items = tuple(
        decision(
            "AEHL", T0 + timedelta(milliseconds=index),
            str(5 + (index % 10) / 1000),
        )
        for index in range(1000)
    )
    started = perf_counter()
    assert all(worker.submit(item) for item in items)
    assert worker.close(timeout_seconds=30)
    elapsed = perf_counter() - started
    metrics = worker.metrics()
    assert metrics.completed == len(items)
    assert metrics.durable_outstanding == 0
    assert metrics.rejected == 0
    assert len(items) / elapsed >= 39
