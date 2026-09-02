from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.strategies.warrior_momentum import (
    MinuteBar,
    SetupDetection,
    SetupState,
    SetupType,
    StopModel,
)
from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.models import PriceBar
from app.trade_intelligence.outcome_engine import OutcomeEngine
from app.trade_intelligence.runtime import TradeIntelligenceRuntimeObserver
from app.trade_intelligence.service import TradeIntelligenceService
from tests.trade_intelligence.conftest import T0, make_experience
from tests.trade_intelligence.test_decision_history import observation
from tests.warrior_momentum.test_shadow_analysis import (
    candidate as warrior_candidate,
    point as warrior_point,
)


def _bar(symbol: str, minute: int, price: str = "10") -> PriceBar:
    value = Decimal(price)
    return PriceBar(
        symbol,
        T0 + timedelta(minutes=minute),
        value,
        value + Decimal("0.10"),
        value - Decimal("0.10"),
        value,
        Decimal("1000"),
    )


def test_soak_orphan_episode_establishes_parent_before_dependent_decision(tmp_path):
    """A failed first snapshot must not leave a parentless accepted child.

    The soak's missing parents had no EXPERIENCE ledger item. This recreates the
    producer path: ``_episode`` succeeds, completed-bar anti-lookahead fails,
    and the next valid observation reuses the provisional episode.
    """

    path = tmp_path / "orphan-ordering.sqlite3"
    runtime = TradeIntelligenceRuntimeObserver(
        enabled=True, environment="TEST", path=path, capacity=16,
    )
    runtime.start()
    setup = SetupDetection(
        SetupType.HIGH_OF_DAY_BREAKOUT,
        SetupState.FORMING,
        Decimal("80"),
        Decimal("10.20"),
        Decimal("9.80"),
        StopModel.BREAKOUT_LEVEL,
    )
    # This bar cannot be complete at T0. Feature extraction raises after the
    # runtime has allocated the episode but before it can submit EXPERIENCE.
    incomplete = MinuteBar(
        "XYZ", T0, Decimal("10"), Decimal("10.1"), Decimal("9.9"),
        Decimal("10"), Decimal("1000"),
    )
    runtime.observe_warrior_decision(
        replace(warrior_point(timestamp=T0), bars=(incomplete,)),
        warrior_candidate(setup=setup, timestamp=T0),
    )

    valid_cutoff = T0 + timedelta(minutes=1)
    runtime.observe_warrior_decision(
        replace(warrior_point(timestamp=valid_cutoff), bars=(incomplete,)),
        warrior_candidate(setup=setup, timestamp=valid_cutoff),
    )
    assert runtime.stop(timeout_seconds=10)

    metrics = runtime.metrics()
    store = ExperienceStore(path)
    assert metrics is not None
    assert metrics.failed == 0
    assert metrics.outstanding == 0
    assert store.count() == 1
    persisted = store.experiences()[0]
    assert len(store.decision_observations(persisted.experience_id)) == 1


def test_rdac_completed_episode_is_not_reactivated_into_outcome_conflict(tmp_path):
    """A late decision cannot recompute already-final immutable horizons.

    This mirrors RDAC: all six horizons are terminal, a later scanner/Warrior
    decision is appended to the same logical episode, and a subsequent minute
    bar arrives. The later cutoff must not be reused under the old
    ``(experience_id, horizon)`` outcome identity.
    """

    path = tmp_path / "rdac-conflict.sqlite3"
    experience = make_experience(symbol="RDAC", episode="soak-rdac", at=T0)
    initial_bars = tuple(_bar("RDAC", minute) for minute in range(31))
    initial_outcomes = OutcomeEngine().evaluate(experience, initial_bars)
    assert len(initial_outcomes) == 6

    store = ExperienceStore(path)
    assert store.put_experience(experience)
    assert store.put_outcomes(initial_outcomes) == (6, 0)

    service = TradeIntelligenceService(path, capacity=16)
    late_decision = observation(experience, seconds=40 * 60)
    assert service.submit_decision(late_decision)
    assert service.observe_completed_bar(_bar("RDAC", 41, "6.35"))
    assert service.close(timeout_seconds=10)

    metrics = service.metrics()
    assert metrics.failed == 0
    assert metrics.outstanding == 0
    assert len(ExperienceStore(path).outcomes(experience.experience_id)) == 6


def test_later_decision_cannot_mix_cutoffs_in_partial_outcome_set(tmp_path):
    path = tmp_path / "rdac-partial.sqlite3"
    experience = make_experience(symbol="RDAC", episode="partial-rdac", at=T0)
    initial = OutcomeEngine().evaluate(experience, (_bar("RDAC", 0),))[0]
    store = ExperienceStore(path)
    assert store.put_experience(experience)
    assert store.put_outcome(initial)

    service = TradeIntelligenceService(path, capacity=16)
    assert service.submit_decision(observation(experience, seconds=40 * 60))
    assert service.observe_completed_bar(_bar("RDAC", 0))
    assert service.observe_completed_bar(_bar("RDAC", 1))
    assert service.close(timeout_seconds=10)

    metrics = service.metrics()
    outcomes = ExperienceStore(path).outcomes(experience.experience_id)
    two_minute = next(item for item in outcomes if item.horizon_minutes == 2)
    assert metrics.failed == metrics.outstanding == 0
    assert two_minute.target_timestamp == T0 + timedelta(minutes=2)
