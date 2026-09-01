from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import sqlite3

import pytest

from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.models import (
    FEATURE_VERSION, SCHEMA_VERSION, DatasetPartition, ResearchGeneration,
    ResearchGenerationCompletion,
)
from tests.trade_intelligence.conftest import T0, make_experience


def generation_v1() -> ResearchGeneration:
    return ResearchGeneration(
        "GENERATION_2026_V1", "ATLAS_TEMPORAL_SPLIT_V1",
        date(2026, 1, 1), date(2026, 6, 30),
        date(2026, 7, 1), date(2026, 7, 31),
        date(2026, 8, 1), date(2026, 8, 31),
        date(2026, 8, 31), FEATURE_VERSION, SCHEMA_VERSION,
        datetime(2026, 9, 1, tzinfo=UTC),
    )


def generation_v2() -> ResearchGeneration:
    return ResearchGeneration(
        "GENERATION_2026_V2", "ATLAS_TEMPORAL_SPLIT_V2",
        date(2026, 1, 1), date(2026, 8, 31),
        date(2026, 9, 1), date(2026, 9, 30),
        date(2026, 10, 1), date(2026, 10, 31),
        date(2026, 10, 31), FEATURE_VERSION, SCHEMA_VERSION,
        datetime(2026, 11, 1, tzinfo=UTC),
        predecessor_generation_id="GENERATION_2026_V1",
    )


def test_generation_definition_and_assignments_are_immutable(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = ExperienceStore(path)
    original = generation_v1()
    assert store.put_research_generation(original)
    assert not store.put_research_generation(original)
    with pytest.raises(ValueError, match="conflicting definition"):
        store.put_research_generation(replace(original, training_start=date(2025, 12, 1)))
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE research_generations SET feature_version='MUTATED'")


def test_same_session_can_never_split_across_partitions(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = ExperienceStore(path)
    store.put_research_generation(generation_v1())
    first = make_experience("ABCD", "one", at=datetime(2026, 7, 15, 14, 30, tzinfo=UTC))
    second = make_experience("WXYZ", "two", at=datetime(2026, 7, 15, 19, 59, tzinfo=UTC))
    store.put_experience(first)
    store.put_experience(second)
    partitions = {
        store.assign_experience_to_generation("GENERATION_2026_V1", item.experience_id, assigned_at=T0)
        for item in (first, second)
    }
    assert partitions == {DatasetPartition.VALIDATION}
    assignments = store.generation_assignments("GENERATION_2026_V1")
    assert {item[1] for item in assignments} == {date(2026, 7, 15)}
    assert {item[2] for item in assignments} == {DatasetPartition.VALIDATION}
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                "UPDATE research_generation_assignments SET partition_name='TRAIN'"
            )


def test_future_data_cannot_enter_frozen_generation(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    store.put_research_generation(generation_v1())
    future = make_experience(at=datetime(2026, 9, 1, 14, 30, tzinfo=UTC))
    store.put_experience(future)
    with pytest.raises(ValueError, match="future|outside"):
        store.assign_experience_to_generation(
            "GENERATION_2026_V1", future.experience_id, assigned_at=T0,
        )
    with pytest.raises(ValueError, match="future"):
        replace(generation_v1(), generation_id="FUTURE", evidence_cutoff=date(2026, 9, 2))


def test_predecessor_holdout_reuse_requires_frozen_completion_and_new_holdout(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    store.put_research_generation(generation_v1())
    with pytest.raises(ValueError, match="before frozen completion"):
        store.put_research_generation(generation_v2())
    completion = ResearchGenerationCompletion(
        "GENERATION_2026_V1", datetime(2026, 9, 2, tzinfo=UTC),
        sha256(b"frozen holdout evaluation").hexdigest(),
    )
    assert store.complete_research_generation(completion)
    assert not store.complete_research_generation(completion)
    with pytest.raises(ValueError, match="immutable"):
        store.complete_research_generation(replace(completion, evaluation_digest="b" * 64))
    assert store.put_research_generation(generation_v2())
    overlapping_holdout = replace(
        generation_v2(), generation_id="BAD_HOLDOUT",
        training_end=date(2026, 7, 31), validation_start=date(2026, 8, 1),
        validation_end=date(2026, 8, 15), holdout_start=date(2026, 8, 16),
        holdout_end=date(2026, 8, 31), evidence_cutoff=date(2026, 8, 31),
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="later untouched holdout"):
        store.put_research_generation(overlapping_holdout)
