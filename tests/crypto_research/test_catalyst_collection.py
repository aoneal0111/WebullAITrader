from __future__ import annotations

from pathlib import Path

import pytest

from app.crypto_research import (
    CryptoCatalystCollectionConfig,
    CryptoCatalystCollectionSink,
    CryptoOutcomeStatus,
    build_crypto_catalyst_outcome_observation,
    replay_crypto_collection,
)
from tests.crypto_research.test_catalyst_outcomes import decision, evidence, outcome, snapshot


def test_collection_is_disabled_by_default_and_creates_no_file(tmp_path: Path):
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(path=tmp_path / "disabled.jsonl"))
    assert sink.start() is False
    assert sink.metrics().collection_enabled is False
    assert not (tmp_path / "disabled.jsonl").exists()


def test_environment_configuration_is_disabled_by_default_and_explicitly_enableable(tmp_path: Path):
    config = CryptoCatalystCollectionConfig.from_environment({"CRYPTO_CATALYST_COLLECTION_ENABLED": "true", "CRYPTO_CATALYST_COLLECTION_PATH": str(tmp_path / "env.jsonl"), "CRYPTO_CATALYST_COLLECTION_QUEUE_CAPACITY": "3"})
    assert config.enabled and config.queue_capacity == 3
    assert not CryptoCatalystCollectionConfig.from_environment({}).enabled


def test_enabled_collection_captures_one_decision_and_four_horizons(tmp_path: Path):
    path = tmp_path / "research.jsonl"
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=path), repository_root=tmp_path / "repo")
    d = decision()
    assert sink.start()
    frozen = snapshot([evidence()])
    assert sink.capture_decision(d, frozen)
    for horizon in (60, 300, 900, 1800):
        assert sink.record_outcome(d, outcome(d, horizon))
    assert sink.flush()
    assert sink.close()
    records = replay_crypto_collection(path)
    assert [item["record_type"] for item in records].count("DECISION") == 1
    assert [item["record_type"] for item in records].count("OUTCOME_OBSERVATION") == 4
    assert records[0]["record_type"] == "RUN_METADATA"
    assert records[-1]["record_type"] == "RUN_END"
    assert sink.metrics().observations_built == 4


def test_later_snapshot_cannot_replace_frozen_decision_snapshot(tmp_path: Path):
    path = tmp_path / "frozen.jsonl"
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=path))
    d = decision()
    first = snapshot([evidence()])
    later = snapshot([evidence(), evidence(provider="FED", key="later")])
    sink.start()
    assert sink.capture_decision(d, first)
    assert sink.record_outcome(d, outcome(d))
    assert sink.flush()
    sink.close()
    records = replay_crypto_collection(path)
    observation = next(item["observation"] for item in records if item["record_type"] == "OUTCOME_OBSERVATION")
    assert observation["catalyst_snapshot_identity"] == first.snapshot_identity
    assert observation["catalyst_snapshot_identity"] != later.snapshot_identity


def test_duplicate_callbacks_and_horizon_callbacks_are_suppressed(tmp_path: Path):
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=tmp_path / "dedup.jsonl"))
    d = decision()
    sink.start()
    frozen = snapshot([evidence()])
    assert sink.capture_decision(d, frozen)
    assert not sink.capture_decision(d, frozen)
    assert sink.record_outcome(d, outcome(d))
    assert not sink.record_outcome(d, outcome(d))
    sink.close()
    metrics = sink.metrics()
    assert metrics.decision_duplicates >= 2
    assert metrics.observations_built == 1


def test_provider_availability_and_truncation_are_retained(tmp_path: Path):
    from app.crypto_research import CryptoCatalystProviderAvailability, CryptoCatalystProviderAvailabilityRecord, CryptoCatalystProviderTier
    blocked = snapshot(availability=(CryptoCatalystProviderAvailabilityRecord("BYBIT", CryptoCatalystProviderAvailability.BLOCKED, CryptoCatalystProviderTier.OFFICIAL_FIRST_PARTY),))
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=tmp_path / "availability.jsonl"))
    d = decision()
    sink.start()
    assert sink.capture_decision(d, blocked)
    assert sink.record_outcome(d, outcome(d))
    sink.close()
    record = next(item["observation"] for item in replay_crypto_collection(tmp_path / "availability.jsonl") if item["record_type"] == "OUTCOME_OBSERVATION")
    assert record["provider_availability_limited"] is True
    assert record["catalyst_truncated"] is False


def test_queue_overflow_is_nonblocking_and_bounded(tmp_path: Path):
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=tmp_path / "overflow.jsonl", queue_capacity=1))
    sink._accepting = True
    d = decision()
    assert sink.capture_decision(d, snapshot([evidence()]))
    assert not sink.capture_decision(decision(opportunity="other"), snapshot([evidence(key="other")]))
    assert sink.metrics().records_dropped >= 1
    assert sink.metrics().queue_depth <= 1
    sink.close(timeout_seconds=0)


def test_writer_failure_is_contained(tmp_path: Path):
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=tmp_path / "failure.jsonl"))
    sink._write = lambda record: (_ for _ in ()).throw(OSError("synthetic writer failure"))
    sink.start()
    d = decision()
    assert sink.capture_decision(d, snapshot([evidence()]))
    sink.flush()
    sink.close()
    assert sink.metrics().write_failures >= 1


def test_replay_is_pure_jsonl_and_authority_flags_are_research_only(tmp_path: Path):
    path = tmp_path / "replay.jsonl"
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=path))
    d = decision()
    sink.start()
    sink.capture_decision(d, snapshot([evidence()]))
    sink.record_outcome(d, outcome(d))
    sink.close()
    records = replay_crypto_collection(path)
    assert all(item["research_only"] is True for item in records)
    assert all(item["execution_authorized"] is False for item in records)


def test_external_repository_path_is_required(tmp_path: Path):
    with pytest.raises(ValueError, match="external"):
        CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=tmp_path / "data.jsonl"), repository_root=tmp_path)
