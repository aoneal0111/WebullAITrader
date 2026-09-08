from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest

from app.assets import AssetType
from app.crypto_research import (
    CryptoCompletedBar,
    CryptoOutcomeDecision,
    CryptoOutcomeJsonlStore,
    CryptoOutcomeStatus,
    CryptoOutcomeTracker,
    CryptoPair,
    SUPPORTED_HORIZONS_SECONDS,
    UNSUPPORTED_HORIZONS_SECONDS,
    label_crypto_outcome,
    replay_crypto_outcomes,
    summarize_crypto_outcomes,
)
from app.research_core import AssetResearchIdentity, EvidenceProvenance


CUTOFF = datetime(2026, 9, 8, 12, tzinfo=UTC)


def decision(memberships=("MICRO_PULLBACK",)) -> CryptoOutcomeDecision:
    identity = AssetResearchIdentity(AssetType.CRYPTO, "SOL/USD", memberships[0], "crypto-phase-c-v1", CUTOFF, "opportunity-1", "episode-1")
    return CryptoOutcomeDecision(identity, "SOL/USD", memberships[0], "crypto-phase-c-v1", CUTOFF, CUTOFF, Decimal("100"), Decimal("95"), Decimal("5"), __import__("app.crypto_research", fromlist=["CryptoRegimeLabel"]).CryptoRegimeLabel.BROAD_RISK_ON, "US", Decimal("2"), Decimal("3"), Decimal("0.4"), Decimal("0.5"), Decimal("66"), Decimal("1.2"), tuple(memberships), "cohort-1", EvidenceProvenance(CUTOFF, CUTOFF))


def bars(symbol="SOL/USD", closes=("104", "108", "112", "110", "115"), *, lows=None, highs=None):
    pair = CryptoPair.configured(symbol)
    lows = lows or tuple(str(Decimal(value) - Decimal("1")) for value in closes)
    highs = highs or tuple(str(Decimal(value) + Decimal("1")) for value in closes)
    result = []
    for index, close in enumerate(closes):
        opened = CUTOFF + timedelta(minutes=index)
        result.append(CryptoCompletedBar(AssetType.CRYPTO, pair.canonical_symbol, pair.provider_symbol, __import__("app.crypto_research", fromlist=["CryptoBarInterval"]).CryptoBarInterval.M1, opened, opened + timedelta(minutes=1), Decimal(close) - Decimal("0.5"), Decimal(highs[index]), Decimal(lows[index]), Decimal(close), None, "fixture", opened + timedelta(minutes=1), opened + timedelta(minutes=1)))
    return tuple(result)


def test_complete_horizon_computes_returns_mae_mfe_r_and_targets():
    outcome = label_crypto_outcome(decision(), bars(), horizon_seconds=300, btc_bars=bars("BTC/USD", ("101", "102", "103", "104", "105")), eth_bars=bars("ETH/USD", ("101", "102", "103", "104", "105")))
    assert outcome.status is CryptoOutcomeStatus.COMPLETE
    assert outcome.forward_return_percent is not None
    assert outcome.mae_r is not None and outcome.mfe_r is not None
    assert outcome.one_r_reached and outcome.two_r_reached and outcome.three_r_reached
    assert outcome.time_to_1r is not None
    assert outcome.btc_relative_forward_return_percent is not None
    assert outcome.eth_relative_forward_return_percent is not None


def test_subminute_horizons_are_explicitly_unavailable():
    for horizon in UNSUPPORTED_HORIZONS_SECONDS:
        assert label_crypto_outcome(decision(), bars(), horizon_seconds=horizon).status is CryptoOutcomeStatus.INSUFFICIENT_PATH_DATA
    assert SUPPORTED_HORIZONS_SECONDS == (60, 300, 900, 1800)


def test_same_bar_stop_and_target_is_conservative_stop_first():
    outcome = label_crypto_outcome(decision(), bars(closes=("104",), lows=("94",), highs=("106",)), horizon_seconds=60)
    assert outcome.status is CryptoOutcomeStatus.COMPLETE
    assert outcome.stop_reached is True
    assert outcome.first_event == "STOP"
    assert outcome.one_r_reached is False


def test_future_bars_cannot_change_earlier_horizon():
    first = label_crypto_outcome(decision(), bars(closes=("104",)), horizon_seconds=60)
    later = label_crypto_outcome(decision(), bars(closes=()), horizon_seconds=60)
    assert first.status is CryptoOutcomeStatus.COMPLETE
    assert later.status is CryptoOutcomeStatus.INSUFFICIENT_PATH_DATA
    assert first.to_dict() != later.to_dict()


def test_tracker_deduplicates_and_releases_completed_episode():
    tracker = CryptoOutcomeTracker(maximum_active_episodes=2)
    item = decision()
    assert tracker.register(item)
    assert not tracker.register(item)
    emitted = tracker.update(item.identity.deterministic_id, bars(closes=tuple("104" for _ in range(31))))
    assert len(emitted) == 7
    assert tracker.metrics().crypto_outcomes_active_episodes == 0
    assert tracker.metrics().crypto_outcomes_duplicates_suppressed >= 1


def test_jsonl_store_is_optional_and_records_are_self_contained(tmp_path):
    outcome = label_crypto_outcome(decision(), bars(), horizon_seconds=60)
    disabled = CryptoOutcomeJsonlStore()
    assert disabled.append(outcome) is False
    store = CryptoOutcomeJsonlStore(tmp_path / "outcomes.jsonl")
    assert store.append(outcome)
    record = (tmp_path / "outcomes.jsonl").read_text(encoding="utf-8").strip()
    assert "schema_version" in record and "SOL/USD" in record


def test_replay_and_summary_are_deterministic_with_sample_guard():
    first = replay_crypto_outcomes(decision(("MICRO_PULLBACK", "BREADTH_ALIGNED_TREND_CONTINUATION")), bars())
    second = replay_crypto_outcomes(decision(("MICRO_PULLBACK", "BREADTH_ALIGNED_TREND_CONTINUATION")), bars())
    assert tuple(item.to_dict() for item in first) == tuple(item.to_dict() for item in second)
    summary = summarize_crypto_outcomes(first)
    assert summary and summary[0].confidence == "INSUFFICIENT_SAMPLE"
    assert summary[0].sample_count == 1


def test_invalid_geometry_is_not_synthetic_zero():
    item = decision()
    with pytest.raises(ValueError, match="risk_per_unit"):
        CryptoOutcomeDecision(item.identity, item.canonical_symbol, item.strategy_id, item.strategy_version, item.decision_timestamp, item.decision_cutoff, Decimal("100"), Decimal("99"), Decimal("0.5"), item.regime_at_decision, item.regime_window_at_decision, item.btc_relative_strength_at_decision, item.eth_relative_strength_at_decision, item.breadth_at_decision, item.eth_correlation_at_decision, item.breadth_at_decision, item.dispersion_at_decision, item.strategy_memberships, item.cohort_identity, item.provenance)


def test_completed_retention_eviction_is_bounded_and_deterministic():
    tracker = CryptoOutcomeTracker(maximum_active_episodes=4, maximum_retained_outcomes=5, maximum_dedup_identities=7)
    decisions = [decision()]
    assert tracker.register(decisions[0])
    emitted = tracker.update(decisions[0].identity.deterministic_id, bars(closes=tuple("104" for _ in range(31))))
    metrics = tracker.metrics()
    assert len(emitted) == 7
    assert metrics.crypto_outcomes_retained <= 5
    assert metrics.crypto_outcomes_retained_high_water <= 5
    assert metrics.crypto_outcomes_evicted > 0
    assert metrics.crypto_outcomes_active_episodes == 0
    assert tracker.update(decisions[0].identity.deterministic_id, bars(closes=tuple("104" for _ in range(31)))) == ()


def test_active_episode_survives_early_cache_eviction_until_later_horizons():
    tracker = CryptoOutcomeTracker(maximum_active_episodes=4, maximum_retained_outcomes=2, maximum_dedup_identities=32)
    item = decision()
    assert tracker.register(item)
    early = tracker.update(item.identity.deterministic_id, bars(closes=("104",)))
    assert early
    assert tracker.metrics().crypto_outcomes_active_episodes == 1
    tracker.update(item.identity.deterministic_id, bars(closes=tuple("104" for _ in range(31))))
    assert tracker.metrics().crypto_outcomes_active_episodes == 0


def test_three_bounds_are_independent_and_dedup_has_its_own_window():
    tracker = CryptoOutcomeTracker(maximum_active_episodes=4, maximum_retained_outcomes=5, maximum_dedup_identities=7)
    base = decision()
    accepted = []
    for index in range(12):
        item = __import__("dataclasses").replace(base, identity=__import__("dataclasses").replace(base.identity, opportunity_id=f"o-{index}", lifecycle_id=f"e-{index}"), cohort_identity=f"c-{index}")
        if tracker.register(item):
            accepted.append(item)
            tracker.update(item.identity.deterministic_id, bars(closes=tuple("104" for _ in range(31))))
    metrics = tracker.metrics()
    assert metrics.crypto_outcomes_active_episodes == 0
    assert metrics.crypto_outcomes_retained <= 5
    assert metrics.crypto_outcomes_dedup_identities <= 7
    assert metrics.crypto_outcomes_dedup_high_water <= 7
    assert metrics.crypto_outcomes_evicted > 0


def test_external_jsonl_survives_memory_eviction_and_explicit_analytics_can_exceed_cache(tmp_path):
    tracker = CryptoOutcomeTracker(maximum_active_episodes=4, maximum_retained_outcomes=2, maximum_dedup_identities=32)
    store = CryptoOutcomeJsonlStore(tmp_path / "durable-outcomes.jsonl")
    item = decision()
    assert tracker.register(item)
    emitted = tracker.update(item.identity.deterministic_id, bars(closes=tuple("104" for _ in range(31))))
    for outcome in emitted:
        assert store.append(outcome)
    tracker.record_persisted(len(emitted))
    assert len((tmp_path / "durable-outcomes.jsonl").read_text(encoding="utf-8").splitlines()) == len(emitted)
    assert tracker.metrics().crypto_outcomes_retained <= 2
    assert len(summarize_crypto_outcomes(emitted)) == 4
    assert tracker.update(item.identity.deterministic_id, bars(closes=tuple("104" for _ in range(31)))) == ()
    assert len((tmp_path / "durable-outcomes.jsonl").read_text(encoding="utf-8").splitlines()) == len(emitted)


def test_dedup_identity_ages_out_only_after_its_separate_bound():
    tracker = CryptoOutcomeTracker(maximum_active_episodes=4, maximum_retained_outcomes=1, maximum_dedup_identities=2)
    base = decision()
    for index in range(3):
        item = __import__("dataclasses").replace(base, identity=__import__("dataclasses").replace(base.identity, opportunity_id=f"age-o-{index}", lifecycle_id=f"age-e-{index}"), cohort_identity=f"age-{index}")
        assert tracker.register(item)
        tracker.update(item.identity.deterministic_id, bars(closes=tuple("104" for _ in range(31))))
    assert tracker.metrics().crypto_outcomes_dedup_identities == 2
