from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event, get_ident

from app.momentum_scanner import (
    CatalystStatus, CatalystType,
    MomentumScannerConfig,
    ScannerObservation,
    evaluate_candidate,
)
from app.paper_trade_experiment import (
    PaperTradeExperimentJournal,
    PaperTradeExperimentWorker,
)
from app.paper_trade_experiment.journal import read_records


T0 = datetime(2026, 8, 31, 11, 0, tzinfo=UTC)


def decision(at: datetime = T0, price: str = "5.00"):
    observation = ScannerObservation(
        symbol="AEHL",
        timestamp=at,
        price=Decimal(price),
        previous_close=Decimal("4"),
        current_volume=Decimal("2000000"),
        average_30_day_volume=Decimal("200000"),
        float_shares=Decimal("5000000"),
        bid=Decimal(price) - Decimal("0.01"),
        ask=Decimal(price) + Decimal("0.01"),
        catalyst=CatalystType.EARNINGS,
        catalyst_headline="earnings",
        tradable=True,
        halted=False,
        last_price_timestamp=at,
        quote_timestamp=at,
        last_price_received_timestamp=at + timedelta(milliseconds=100),
        quote_received_timestamp=at + timedelta(milliseconds=100),
    )
    return replace(
        evaluate_candidate(observation, MomentumScannerConfig()),
        observed_at=at + timedelta(milliseconds=200),
        scanner_rank=1,
    )


def test_slow_research_never_runs_or_blocks_on_market_caller(tmp_path) -> None:
    entered = Event()
    release = Event()
    caller_thread = get_ident()
    worker_thread = []

    class SlowJournal:
        def __init__(self, _path):
            pass

        def record_scanner_decision(self, _decision, **_kwargs):
            worker_thread.append(get_ident())
            entered.set()
            assert release.wait(2)

    worker = PaperTradeExperimentWorker(
        tmp_path / "research.sqlite3",
        execution_environment="TEST",
        capacity=8,
        journal_factory=SlowJournal,
    )
    assert worker.submit(decision()) is True
    assert entered.wait(2)
    # This second capture is a nonblocking put while retrospective work is held.
    assert worker.submit(decision(T0 + timedelta(seconds=1), "5.10")) is True
    assert worker_thread == [worker.thread.ident]
    assert worker.thread.ident != caller_thread
    release.set()
    assert worker.close(timeout_seconds=2)
    assert worker.metrics().completed == 2
    assert not worker.thread.is_alive()


def test_metrics_identify_capture_path_activity_and_incomplete_evidence(
    tmp_path,
) -> None:
    path = tmp_path / "capture-health.sqlite3"
    worker = PaperTradeExperimentWorker(
        path, execution_environment="TEST",
    )
    complete = decision()
    missing = replace(
        decision(T0 + timedelta(seconds=1), "5.10"),
        bid=None,
        ask=None,
        float_shares=None,
        halted=None,
        tradable=None,
        catalyst_status=CatalystStatus.TRUE,
        catalyst_source=None,
        catalyst_published_at=None,
        metrics=replace(
            decision().metrics,
            spread_percent=None,
        ),
    )
    assert worker.submit(complete) is True
    assert worker.submit(missing) is True
    assert worker.submit(replace(complete, timestamp=None, price=None)) is False
    assert worker.close(timeout_seconds=2)

    metrics = worker.metrics()
    reasons = dict(metrics.evidence_incomplete_counts)
    assert metrics.journal_path == str(path.resolve())
    assert metrics.decisions_received == 3
    assert metrics.complete_decisions_received == 2
    assert metrics.incomplete_decisions_received == 1
    assert metrics.candidate_creations == 2
    assert metrics.completed == 2
    assert metrics.last_decision_received_at is not None
    assert metrics.last_work_completed_at is not None
    assert reasons["CATALYST_EVIDENCE_UNAVAILABLE"] == 1
    assert reasons["QUOTE_EVIDENCE_NOT_RECORDED"] == 1
    assert reasons["SPREAD_EVIDENCE_NOT_RECORDED"] == 1
    assert reasons["FLOAT_EVIDENCE_NOT_RECORDED"] == 1
    assert reasons["HALT_STATE_NOT_RECORDED"] == 1
    assert reasons["CATALYST_SOURCE_NOT_RECORDED"] == 1
    assert reasons["CATALYST_PUBLISHED_AT_NOT_RECORDED"] == 1
    assert reasons["DECISION_TIMESTAMP_NOT_RECORDED"] == 1
    assert reasons["LAST_PRICE_NOT_RECORDED"] == 1


def test_bounded_saturation_is_explicit_and_capture_recovers(tmp_path) -> None:
    entered = Event()
    release = Event()
    drained = Event()

    class SlowJournal:
        calls = 0

        def __init__(self, _path):
            pass

        def record_scanner_decision(self, _decision, **_kwargs):
            type(self).calls += 1
            entered.set()
            if type(self).calls == 1:
                assert release.wait(2)
            if type(self).calls == 2:
                drained.set()

    worker = PaperTradeExperimentWorker(
        tmp_path / "research.sqlite3",
        execution_environment="TEST",
        capacity=1,
        journal_factory=SlowJournal,
    )
    assert worker.submit(decision())
    assert entered.wait(2)
    assert worker.submit(decision(T0 + timedelta(seconds=1), "5.10"))
    assert worker.submit(decision(T0 + timedelta(seconds=2), "5.20")) is False
    metrics = worker.metrics()
    assert not metrics.failed and metrics.accepting
    assert metrics.queue_high_water == 1
    assert metrics.rejected == 1
    assert metrics.pressure_episodes == 1
    release.set()
    assert drained.wait(2)
    assert worker.submit(decision(T0 + timedelta(seconds=3), "5.30"))
    assert worker.close(timeout_seconds=2)
    assert worker.metrics().completed == 3


def test_august31_backlog_shape_is_absorbed_without_history_work_on_caller(
    tmp_path,
) -> None:
    entered = Event()
    release = Event()

    class GrowingHistoryJournal:
        calls = 0

        def __init__(self, _path):
            pass

        def record_scanner_decision(self, _decision, **_kwargs):
            type(self).calls += 1
            entered.set()
            assert release.wait(3)

    worker = PaperTradeExperimentWorker(
        tmp_path / "research.sqlite3",
        execution_environment="TEST",
        capacity=128,
        journal_factory=GrowingHistoryJournal,
    )
    assert worker.submit(decision())
    assert entered.wait(2)
    for index in range(1, 101):
        assert worker.submit(decision(
            T0 + timedelta(milliseconds=index),
            str(Decimal("5") + Decimal(index) / Decimal("1000")),
        ))
    assert GrowingHistoryJournal.calls == 1
    assert worker.metrics().queue_depth == 100
    release.set()
    assert worker.close(timeout_seconds=5)
    assert worker.metrics().completed == 101


def test_worker_failure_is_observable_and_does_not_escape_submitter(tmp_path) -> None:
    failed = Event()

    class FailingJournal:
        def __init__(self, _path):
            pass

        def record_scanner_decision(self, _decision, **_kwargs):
            failed.set()
            raise OSError("sqlite unavailable")

    worker = PaperTradeExperimentWorker(
        tmp_path / "research.sqlite3",
        execution_environment="TEST",
        journal_factory=FailingJournal,
    )
    assert worker.submit(decision())
    assert failed.wait(2)
    assert worker.close(timeout_seconds=2)
    metrics = worker.metrics()
    assert metrics.failed
    assert metrics.failures == 1
    assert metrics.completed == 0
    assert not worker.thread.is_alive()


def test_shutdown_timeout_rejects_pending_work_and_remains_recoverable(
    tmp_path,
) -> None:
    entered = Event()
    release = Event()

    class StuckJournal:
        def __init__(self, _path):
            pass

        def record_scanner_decision(self, _decision, **_kwargs):
            entered.set()
            assert release.wait(2)

    worker = PaperTradeExperimentWorker(
        tmp_path / "research.sqlite3",
        execution_environment="TEST",
        capacity=4,
        journal_factory=StuckJournal,
    )
    assert worker.submit(decision())
    assert entered.wait(2)
    assert worker.submit(decision(T0 + timedelta(seconds=1), "5.10"))
    assert worker.close(timeout_seconds=0) is False
    metrics = worker.metrics()
    assert metrics.failed and metrics.rejected == 1 and metrics.queue_depth == 0
    release.set()
    assert worker.close(timeout_seconds=2)
    assert not worker.thread.is_alive()


def test_stop_drains_fifo_and_preserves_reference_labels(tmp_path) -> None:
    sequence = (
        decision(T0, "5.00"),
        decision(T0 + timedelta(minutes=1), "5.50"),
        decision(T0 + timedelta(minutes=5), "4.50"),
        decision(T0 + timedelta(minutes=15), "5.25"),
        decision(T0 + timedelta(minutes=30), "6.00"),
    )
    reference_path = tmp_path / "reference.sqlite3"
    actual_path = tmp_path / "actual.sqlite3"
    reference = PaperTradeExperimentJournal(reference_path)
    expected_record = reference.record_candidate(
        sequence[0], execution_environment="TEST", market_session="PREMARKET"
    )
    for item in sequence[1:]:
        reference.observe_price(item.symbol, item.timestamp, item.price)

    worker = PaperTradeExperimentWorker(
        actual_path,
        execution_environment="TEST",
        capacity=len(sequence),
    )
    for item in sequence:
        assert worker.submit(item)
    assert worker.close(timeout_seconds=5)

    expected = read_records(reference_path)
    actual = read_records(actual_path)
    assert len(expected) == len(actual) == 1
    assert actual[0].labels == reference.get(expected_record.candidate_id).labels
    assert actual[0].execution == expected[0].execution
    assert {
        key: value for key, value in actual[0].features.items()
        if key != "logical_candidate_identity"
    } == expected[0].features
    assert worker.metrics().completed == len(sequence)


def test_price_backlog_keeps_only_latest_pending_observation_per_symbol(
    tmp_path,
) -> None:
    entered = Event()
    release = Event()
    observed = []

    class SlowPriceJournal:
        def __init__(self, _path):
            pass

        def observe_price(self, symbol, timestamp, price):
            observed.append((symbol, timestamp, price))
            entered.set()
            if len(observed) == 1:
                assert release.wait(2)

    worker = PaperTradeExperimentWorker(
        tmp_path / "latest-only.sqlite3",
        execution_environment="TEST",
        capacity=8,
        journal_factory=SlowPriceJournal,
    )
    assert worker.observe_price("VEEE", T0, Decimal("10"))
    assert entered.wait(2)
    for index in range(1, 101):
        assert worker.observe_price(
            "VEEE",
            T0 + timedelta(milliseconds=index),
            Decimal("10") + Decimal(index) / Decimal("100"),
        )

    metrics = worker.metrics()
    assert metrics.queue_depth == 0
    assert metrics.coalesced == 100
    release.set()
    assert worker.close(timeout_seconds=2)
    assert len(observed) == 2
    assert observed[-1][2] == Decimal("11")
