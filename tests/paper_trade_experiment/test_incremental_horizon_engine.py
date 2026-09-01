from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event

from app.momentum_scanner import (
    CatalystType,
    MomentumScannerConfig,
    ScannerObservation,
    evaluate_candidate,
)
from app.paper_trade_experiment import (
    HORIZONS_SECONDS,
    PaperTradeExperimentJournal,
    PaperTradeExperimentWorker,
    prepare_research_work,
)


T0 = datetime(2026, 8, 31, 14, 0, tzinfo=UTC)


def decision(symbol: str, at: datetime, price: str):
    value = Decimal(price)
    observation = ScannerObservation(
        symbol=symbol,
        timestamp=at,
        price=value,
        previous_close=Decimal("4"),
        current_volume=Decimal("2000000"),
        average_30_day_volume=Decimal("200000"),
        float_shares=Decimal("5000000"),
        bid=value - Decimal("0.01"),
        ask=value + Decimal("0.01"),
        catalyst=CatalystType.EARNINGS,
        catalyst_headline="earnings",
        tradable=True,
        halted=False,
        last_price_timestamp=at,
        quote_timestamp=at,
        last_price_received_timestamp=at + timedelta(milliseconds=1),
        quote_received_timestamp=at + timedelta(milliseconds=1),
    )
    return replace(
        evaluate_candidate(observation, MomentumScannerConfig()),
        observed_at=at + timedelta(milliseconds=2),
        scanner_rank=1,
    )


def _legacy_labels(sequence):
    candidates: list[dict[str, object]] = []
    latest: dict[str, tuple[datetime, Decimal]] = {}
    for item in sequence:
        assert item.timestamp is not None and item.price is not None
        observed_at = item.last_price_timestamp or item.timestamp
        observation = (observed_at, item.price)
        if latest.get(item.symbol) != observation:
            for candidate in candidates:
                if candidate["symbol"] != item.symbol:
                    continue
                elapsed = (observed_at - candidate["timestamp"]).total_seconds()
                if elapsed < 0:
                    continue
                labels = candidate["labels"]
                reference = candidate["reference"]
                move = (item.price - reference) / reference
                if elapsed <= HORIZONS_SECONDS["30m"]:
                    labels["mfe"] = str(max(Decimal(labels.get("mfe", "0")), move))
                    labels["mae"] = str(min(Decimal(labels.get("mae", "0")), move))
                labels["last_observed_at"] = observed_at.isoformat()
                for name, seconds in HORIZONS_SECONDS.items():
                    key = f"price_after_{name}"
                    if elapsed >= seconds and key not in labels:
                        labels[key] = str(item.price)
                        labels[f"return_after_{name}"] = str(move)
                labels["outcome_status"] = (
                    "COMPLETE" if "price_after_30m" in labels else "PENDING"
                )
            latest[item.symbol] = observation
        candidates.append({
            "symbol": item.symbol,
            "timestamp": item.timestamp,
            "reference": item.price,
            "labels": {},
        })
    return [item["labels"] for item in candidates]


def test_incremental_engine_is_exactly_equivalent_to_legacy_horizons(tmp_path) -> None:
    sequence = (
        decision("AEHL", T0, "5.00"),
        decision("AEHL", T0, "5.10"),
        decision("BIVI", T0, "4.00"),
        decision("AEHL", T0 + timedelta(seconds=30), "5.50"),
        decision("AEHL", T0 + timedelta(minutes=1), "5.25"),
        decision("BIVI", T0 + timedelta(minutes=1), "4.00"),
        decision("AEHL", T0 + timedelta(minutes=2), "5.25"),
        decision("BIVI", T0 + timedelta(minutes=5), "3.50"),
        decision("AEHL", T0 + timedelta(minutes=5), "4.50"),
        decision("AEHL", T0 + timedelta(minutes=4), "4.75"),
        decision("AEHL", T0 + timedelta(minutes=15), "5.75"),
        decision("BIVI", T0 + timedelta(minutes=30), "4.25"),
        decision("AEHL", T0 + timedelta(minutes=30), "6.00"),
    )
    expected = _legacy_labels(sequence)
    journal = PaperTradeExperimentJournal(tmp_path / "incremental.sqlite3")
    actual_ids = [
        journal.record_scanner_decision(item, execution_environment="TEST").candidate_id
        for item in sequence
    ]
    actual = [journal.get(candidate_id).labels for candidate_id in actual_ids]

    assert actual == expected
    snapshot = journal.completeness_snapshot()
    complete = sum(
        item.get("outcome_status") == "COMPLETE" for item in expected
    )
    assert snapshot["complete_candidate_count"] == complete
    assert snapshot["active_candidate_count"] == len(sequence) - complete


def test_complete_candidates_permanently_leave_active_query(tmp_path) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / "active.sqlite3")
    record = journal.record_candidate(decision("AEHL", T0, "5.00"))
    journal.observe_price("AEHL", T0 + timedelta(minutes=30), Decimal("6.00"))

    assert journal.get(record.candidate_id).labels["outcome_status"] == "COMPLETE"
    assert journal.completeness_snapshot()["active_candidate_count"] == 0
    assert journal.observe_price(
        "AEHL", T0 + timedelta(minutes=31), Decimal("7.00")
    ) == 0
    plan = " ".join(journal.active_query_plan("AEHL", T0 + timedelta(hours=1)))
    assert "research_active_symbol_time" in plan
    assert "experiment_candidates_symbol_time" not in plan


def test_legacy_bootstrap_is_idempotent_and_only_retains_pending(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    journal = PaperTradeExperimentJournal(path)
    pending = journal.record_candidate(decision("AEHL", T0, "5.00"))
    complete = journal.record_candidate(decision("BIVI", T0, "4.00"))
    journal.observe_price("BIVI", T0 + timedelta(minutes=30), Decimal("4.50"))
    journal.close()

    connection = sqlite3.connect(path)
    connection.execute(
        "DELETE FROM experiment_metadata WHERE key='incremental_engine_version'"
    )
    connection.execute("DROP TABLE research_active_candidates")
    connection.execute("DROP TABLE research_work_items")
    connection.commit()
    connection.close()

    migrated = PaperTradeExperimentJournal(path)
    assert migrated.completeness_snapshot()["active_candidate_count"] == 1
    assert migrated.get(pending.candidate_id).labels == {}
    assert migrated.get(complete.candidate_id).labels["outcome_status"] == "COMPLETE"
    migrated.close()
    reopened = PaperTradeExperimentJournal(path)
    assert reopened.completeness_snapshot()["active_candidate_count"] == 1


def test_work_ledger_rolls_back_and_remains_recoverable(tmp_path) -> None:
    path = tmp_path / "rollback.sqlite3"
    journal = PaperTradeExperimentJournal(path)
    prepared = prepare_research_work(
        decision("AEHL", T0, "5.00"), execution_environment="TEST"
    )
    malformed = replace(prepared, payload_json=json.dumps({"features": {}}))
    journal.checkpoint_work_items((malformed,))

    try:
        journal.process_prepared_work(malformed)
    except (KeyError, ValueError):
        pass
    else:
        raise AssertionError("malformed durable work unexpectedly completed")

    snapshot = journal.completeness_snapshot()
    assert snapshot["items_completed"] == 0
    assert snapshot["durable_outstanding"] == 1


def test_timeout_checkpoint_resumes_without_loss_or_duplicates(tmp_path) -> None:
    path = tmp_path / "restart.sqlite3"
    entered = Event()
    release = Event()

    class InterruptedJournal(PaperTradeExperimentJournal):
        def process_prepared_batch(self, work):
            entered.set()
            assert release.wait(2)
            raise sqlite3.OperationalError("injected interruption")

    first = PaperTradeExperimentWorker(
        path,
        execution_environment="TEST",
        capacity=64,
        journal_factory=InterruptedJournal,
    )
    sequence = tuple(
        decision("AEHL", T0 + timedelta(seconds=index), str(5 + index / 1000))
        for index in range(40)
    )
    assert all(first.submit(item) for item in sequence)
    assert entered.wait(2)
    assert first.close(timeout_seconds=0) is False
    release.set()
    assert first.close(timeout_seconds=2)

    checkpoint = PaperTradeExperimentJournal(path)
    before = checkpoint.completeness_snapshot()
    checkpoint.close()
    assert before["items_accepted"] == len(sequence)
    assert before["items_completed"] == 0
    assert before["durable_outstanding"] == len(sequence)

    resumed = PaperTradeExperimentWorker(
        path, execution_environment="TEST", capacity=64
    )
    assert resumed.close(timeout_seconds=10)
    after_journal = PaperTradeExperimentJournal(path)
    after = after_journal.completeness_snapshot()
    assert after["items_accepted"] == len(sequence)
    assert after["items_completed"] == len(sequence)
    assert after["durable_outstanding"] == 0
    assert len(after_journal.records()) == 1
    assert resumed.metrics().resumed == len(sequence)


def test_duplicate_work_identity_is_idempotent(tmp_path) -> None:
    path = tmp_path / "duplicate.sqlite3"
    item = decision("AEHL", T0, "5.00")
    worker = PaperTradeExperimentWorker(path, execution_environment="TEST")
    assert worker.submit(item)
    assert worker.submit(item)
    assert worker.close(timeout_seconds=5)

    journal = PaperTradeExperimentJournal(path)
    assert len(journal.records()) == 1
    assert journal.completeness_snapshot()["items_accepted"] == 1
