from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.features import extract_completed_bar_features
from app.trade_intelligence.models import (
    AtlasDecision, DecisionTimeSnapshot, OpportunityKey, PriceBar,
)
from tests.trade_intelligence.conftest import T0, make_experience


def bar(minute, *, high="10.2", low="9.9", close="10.1", symbol="ABCD"):
    return PriceBar(symbol, T0 + timedelta(minutes=minute), Decimal("10"), Decimal(high), Decimal(low), Decimal(close), Decimal("1000"))


def test_stable_identity_excludes_ticks_and_store_rejects_mutation(tmp_path):
    first = make_experience()
    same_episode = replace(first, source_event_identity="a-later-tick")
    assert first.experience_id == same_episode.experience_id
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    assert store.put_experience(first)
    assert not store.put_experience(first)
    with pytest.raises(ValueError, match="conflicting content"):
        store.put_experience(same_episode)
    assert store.count() == 1


def test_thousands_of_ordinary_ticks_do_not_change_logical_cardinality(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    item = make_experience()
    for _ in range(5000):
        store.put_experience(item)
    assert store.count() == 1


def test_multiple_symbols_and_overlapping_opportunities_have_distinct_identity(tmp_path):
    values = (
        make_experience("ABCD", "one"), make_experience("ABCD", "two"),
        make_experience("WXYZ", "one"),
    )
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    assert all(store.put_experience(item) for item in values)
    assert len({item.experience_id for item in values}) == store.count() == 3


def test_anti_lookahead_rejects_future_source_and_feature_timestamps():
    with pytest.raises(ValueError, match="anti-lookahead"):
        DecisionTimeSnapshot(decision_timestamp=T0, source_timestamp=T0 + timedelta(seconds=1))
    with pytest.raises(ValueError, match="anti-lookahead"):
        DecisionTimeSnapshot(
            decision_timestamp=T0, features=(("x", Decimal("1")),),
            feature_source_timestamps=(("x", T0 + timedelta(microseconds=1)),),
        )


def test_completed_bar_features_are_immutable_and_unavailable_is_not_zero():
    bars = (bar(-2), bar(-1))
    features, sources = extract_completed_bar_features(bars, decision_cutoff=T0)
    assert dict(features)["distance_from_vwap_percent"] is None
    assert all(timestamp <= T0 for _, timestamp in sources)
    with pytest.raises(ValueError, match="lookahead"):
        extract_completed_bar_features((bar(0),), decision_cutoff=T0)


def test_temporal_partition_keeps_session_together():
    one = make_experience("ABCD", "one")
    two = make_experience("WXYZ", "two", at=T0 + timedelta(hours=2))
    assert one.partition == two.partition
    assert one.key.session_date == two.key.session_date
    train = make_experience(at=datetime(2026, 6, 30, 14, 30, tzinfo=UTC))
    validation = make_experience(at=datetime(2026, 7, 15, 14, 30, tzinfo=UTC))
    holdout = make_experience(at=datetime(2026, 8, 1, 14, 30, tzinfo=UTC))
    assert [item.partition.value for item in (train, validation, holdout)] == [
        "TRAIN", "VALIDATION", "HOLDOUT",
    ]


def test_persisted_snapshot_round_trip_is_frozen(tmp_path, experience):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    store.put_experience(experience)
    recovered = store.get_experience(experience.experience_id)
    assert recovered == experience
    with pytest.raises((AttributeError, TypeError)):
        recovered.snapshot.last_price = Decimal("99")
