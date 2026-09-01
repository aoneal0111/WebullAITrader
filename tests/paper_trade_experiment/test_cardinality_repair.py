from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.paper_trade_experiment import (
    PaperTradeExperimentJournal,
    PaperTradeExperimentWorker,
)
from tests.paper_trade_experiment.test_incremental_horizon_engine import (
    T0,
    decision,
)


def _without_identity(features):
    return {
        key: value for key, value in features.items()
        if key != "logical_candidate_identity"
    }


def _stable_decision(symbol: str, at, price: str):
    base = decision(symbol, T0, "5.00" if symbol == "AEHL" else "4.00")
    value = Decimal(price)
    return replace(
        base,
        timestamp=at,
        observed_at=at + timedelta(milliseconds=2),
        price=value,
        bid=value - Decimal("0.01"),
        ask=value + Decimal("0.01"),
        last_price_timestamp=at,
        quote_timestamp=at,
        last_price_received_timestamp=at + timedelta(milliseconds=1),
        quote_received_timestamp=at + timedelta(milliseconds=1),
    )


def test_repeated_ticks_advance_one_logical_candidate(tmp_path) -> None:
    path = tmp_path / "bounded.sqlite3"
    worker = PaperTradeExperimentWorker(path, execution_environment="TEST")
    sequence = tuple(
        decision("AEHL", T0 + timedelta(seconds=index), str(5 + index / 1000))
        for index in range(120)
    )

    assert all(worker.submit(item) for item in sequence)
    assert worker.close(timeout_seconds=10)

    journal = PaperTradeExperimentJournal(path)
    assert len(journal.records()) == 1
    metrics = worker.metrics()
    assert metrics.candidate_creations == 1
    assert metrics.observations_accepted == len(sequence)
    assert metrics.rejected == 0
    assert metrics.durable_outstanding == 0


def test_only_authoritative_state_transition_creates_candidate(tmp_path) -> None:
    path = tmp_path / "transitions.sqlite3"
    worker = PaperTradeExperimentWorker(path, execution_environment="TEST")
    initial = replace(
        decision("AEHL", T0, "5.00"),
        source_event_identity="WEBULL:1:TRADE",
    )
    continuous_change = replace(
        decision("AEHL", T0 + timedelta(seconds=1), "5.10"),
        score=initial.score + 100,
        scanner_rank=99,
        source_event_identity="WEBULL:2:TRADE",
    )
    rejected_state = replace(
        decision("AEHL", T0 + timedelta(seconds=2), "5.20"),
        qualified=False,
        passed_rules=tuple(
            rule for rule in initial.passed_rules if rule != "price_range"
        ),
        failed_rules=("price_range",),
        technical_qualifies_without_catalyst=False,
        technical_passed_rules=tuple(
            rule for rule in initial.technical_passed_rules
            if rule != "price_range"
        ),
        technical_failed_rules=("price_range",),
        cohort_flags=(),
        source_event_identity="WEBULL:3:TRADE",
    )

    assert worker.submit(initial)
    assert worker.submit(continuous_change)
    assert worker.submit(rejected_state)
    assert worker.close(timeout_seconds=10)

    records = PaperTradeExperimentJournal(path).records()
    assert len(records) == 2
    assert worker.metrics().candidate_creations == 2
    assert [record.features["normal_qualifies"] for record in records] == [True, False]


def test_batched_observations_match_decimal_horizon_oracle(tmp_path) -> None:
    sequence = (
        _stable_decision("AEHL", T0, "5.00"),
        _stable_decision("BIVI", T0, "4.00"),
        _stable_decision("AEHL", T0, "5.00"),  # exact duplicate
        _stable_decision("AEHL", T0 + timedelta(seconds=30), "5.75"),
        _stable_decision("BIVI", T0 + timedelta(minutes=1), "4.00"),  # flat
        _stable_decision("AEHL", T0 + timedelta(minutes=1), "5.25"),
        _stable_decision("AEHL", T0 + timedelta(minutes=5), "4.25"),
        _stable_decision("AEHL", T0 + timedelta(minutes=4), "4.50"),
        _stable_decision("BIVI", T0 + timedelta(minutes=5, seconds=1), "3.50"),
        _stable_decision("AEHL", T0 + timedelta(minutes=15), "6.50"),
        _stable_decision("BIVI", T0 + timedelta(minutes=30), "4.25"),
        _stable_decision("AEHL", T0 + timedelta(minutes=31), "6.00"),
    )
    oracle = PaperTradeExperimentJournal(tmp_path / "oracle.sqlite3")
    oracle_ids = {}
    seen = {}
    for item in sequence:
        observation = (item.last_price_timestamp or item.timestamp, item.price)
        if item.symbol not in oracle_ids:
            oracle_ids[item.symbol] = oracle.record_candidate(
                item, market_session="REGULAR", execution_environment="TEST"
            ).candidate_id
        elif seen.get(item.symbol) != observation:
            oracle.observe_price(item.symbol, observation[0], observation[1])
        seen[item.symbol] = observation

    actual_path = tmp_path / "actual.sqlite3"
    worker = PaperTradeExperimentWorker(actual_path, execution_environment="TEST")
    assert all(worker.submit(item) for item in sequence)
    assert worker.close(timeout_seconds=10)
    actual = {
        record.symbol: record
        for record in PaperTradeExperimentJournal(actual_path).records()
    }

    assert set(actual) == set(oracle_ids)
    for symbol, candidate_id in oracle_ids.items():
        expected = oracle.get(candidate_id)
        assert actual[symbol].labels == expected.labels
        assert _without_identity(actual[symbol].features) == expected.features


def test_duplicate_replay_is_idempotent_across_restart(tmp_path) -> None:
    path = tmp_path / "replay.sqlite3"
    item = replace(
        decision("AEHL", T0, "5.00"),
        source_event_identity="WEBULL:77:TRADE",
    )
    first = PaperTradeExperimentWorker(path, execution_environment="TEST")
    assert first.submit(item)
    assert first.close(timeout_seconds=10)
    second = PaperTradeExperimentWorker(path, execution_environment="TEST")
    assert second.submit(item)
    assert second.close(timeout_seconds=10)

    journal = PaperTradeExperimentJournal(path)
    assert len(journal.records()) == 1
    assert journal.completeness_snapshot()["items_accepted"] == 1


def test_authoritative_stream_reset_starts_a_new_episode(tmp_path) -> None:
    path = tmp_path / "reset.sqlite3"
    worker = PaperTradeExperimentWorker(path, execution_environment="TEST")
    first = replace(
        decision("AEHL", T0, "5.00"),
        source_event_identity="WEBULL:1:TRADE",
    )
    after_reset = replace(
        first,
        timestamp=T0 + timedelta(seconds=1),
        last_price_timestamp=T0 + timedelta(seconds=1),
        source_event_identity="WEBULL:2:TRADE",
    )
    assert worker.submit(first)
    worker.reset_symbol("AEHL")
    assert worker.submit(after_reset)
    assert worker.close(timeout_seconds=10)

    assert len(PaperTradeExperimentJournal(path).records()) == 2
    assert worker.metrics().candidate_creations == 2


def test_legacy_outstanding_is_quarantined_in_place(tmp_path) -> None:
    path = tmp_path / "legacy-outstanding.sqlite3"
    journal = PaperTradeExperimentJournal(path)
    with journal._connection:
        journal._connection.execute(
            """INSERT INTO research_work_items(
               work_id,payload_json,enqueued_at,state
               ) VALUES(?,?,?,'CHECKPOINTED')""",
            (
                "legacy-work-1",
                json.dumps({"features": {"symbol": "AEHL"}}),
                T0.isoformat(),
            ),
        )
    journal.close()

    worker = PaperTradeExperimentWorker(path, execution_environment="TEST")
    assert worker.close(timeout_seconds=10)
    reopened = PaperTradeExperimentJournal(path)
    snapshot = reopened.completeness_snapshot()
    row = reopened._connection.execute(
        "SELECT state FROM research_work_items WHERE work_id='legacy-work-1'"
    ).fetchone()
    assert row["state"] == "CHECKPOINTED"
    assert snapshot["legacy_outstanding_count"] == 1
    assert snapshot["durable_outstanding"] == 1
    assert worker.metrics().resumed == 0
    assert worker.metrics().legacy_outstanding == 1
