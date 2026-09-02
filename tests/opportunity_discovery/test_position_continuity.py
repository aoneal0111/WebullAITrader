from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.opportunity_discovery import (
    AuthoritativePositionReference,
    DetectionState,
    NormalizedOpportunity,
    PositionThesisState,
    StrategyFamily,
    StrategyMembership,
    StrategyTransitionType,
    add_on_research_candidate,
    correlate_position,
    observe_position_opportunity,
    observe_position_strategies,
    position_learning_features,
    strategy_transition_edges,
)
from tests.opportunity_discovery.conftest import T0


def membership(strategy_id, state=DetectionState.DETECTED, version="v1"):
    return StrategyMembership(
        strategy_id, version, StrategyFamily.CONTINUATION, state,
        f"episode:{strategy_id}", f"anchor:{strategy_id}", Decimal("11"),
        Decimal("11"), Decimal("10"), ("SYNTHETIC_STRUCTURE",),
    )


def opportunity(identity, minute, *memberships):
    return NormalizedOpportunity(
        identity, "ABCD", T0.date(), "REGULAR", T0 + timedelta(minutes=minute),
        f"ABCD|window:{minute // 15}", memberships[0].strategy_id,
        tuple(memberships), Decimal("11"), Decimal("10"), True,
    )


def opened_position():
    original = opportunity("opp:entry", 0, membership("HIGH_OF_DAY_BREAKOUT"))
    projection = correlate_position(
        position_id="paper-position:ABCD:1",
        authoritative_reference=AuthoritativePositionReference(
            "paper-order-book", "paper", "paper:ABCD", "ABCD", "warrior:lifecycle:1",
        ),
        opportunity=original,
        entry_strategy_id="HIGH_OF_DAY_BREAKOUT",
        entry_strategy_version="v1",
        entry_timestamp=T0,
        entry_price=Decimal("11"),
        initial_structural_stop=Decimal("10"),
        initial_risk=Decimal("1"),
    )
    return projection, original


def test_hod_to_first_pullback_to_higher_low_keeps_one_position():
    projection, _ = opened_position()
    first = opportunity("opp:pullback", 8, membership("FIRST_PULLBACK"))
    projection = observe_position_opportunity(projection, first)
    assert projection.position_id == "paper-position:ABCD:1"
    assert projection.entry_strategy_id == "HIGH_OF_DAY_BREAKOUT"
    assert projection.position_open
    assert {item.transition_type for item in projection.strategy_transition_history} == {
        StrategyTransitionType.STRATEGY_JOINED,
        StrategyTransitionType.STRATEGY_LEFT,
    }

    higher_low = opportunity(
        "opp:continuation", 12,
        membership("FIRST_PULLBACK"), membership("HIGHER_LOW_CONTINUATION"),
    )
    projection = observe_position_opportunity(projection, higher_low)
    assert projection.position_id == "paper-position:ABCD:1"
    assert projection.entry_strategy_id == "HIGH_OF_DAY_BREAKOUT"
    assert {item.strategy_id for item in projection.current_strategy_memberships} == {
        "FIRST_PULLBACK", "HIGHER_LOW_CONTINUATION",
    }
    assert projection.current_thesis_state is PositionThesisState.THESIS_STRENGTHENING


def test_original_strategy_disappearance_never_closes_or_restarts_position():
    projection, _ = opened_position()
    evolved = observe_position_strategies(
        projection, (membership("BULL_FLAG"),),
        decision_cutoff=T0 + timedelta(minutes=10), opportunity_id="opp:bull-flag",
    )
    assert evolved.position_id == projection.position_id
    assert evolved.original_opportunity_id == projection.original_opportunity_id
    assert evolved.entry_strategy_id == projection.entry_strategy_id
    assert evolved.entry_timestamp == projection.entry_timestamp
    assert evolved.entry_price == projection.entry_price
    assert evolved.position_open is True


def test_invalidation_changes_thesis_only_and_preserves_stop_and_position():
    projection, _ = opened_position()
    invalid = replace(
        projection.current_strategy_memberships[0], state=DetectionState.INVALIDATED,
    )
    evolved = observe_position_strategies(
        projection, (invalid,), decision_cutoff=T0 + timedelta(minutes=1),
    )
    assert evolved.current_thesis_state is PositionThesisState.THESIS_INVALIDATED
    assert evolved.strategy_transition_history[-1].transition_type is StrategyTransitionType.STRATEGY_INVALIDATED
    assert evolved.position_open
    assert evolved.initial_structural_stop == projection.initial_structural_stop
    assert evolved.initial_risk == projection.initial_risk


def test_opportunity_window_rollover_is_not_position_lifetime():
    projection, _ = opened_position()
    later = opportunity(
        "opp:window-2", 31,
        membership("BREAKOUT_RETEST_CONTINUATION"),
        membership("RANGE_COMPRESSION_BREAKOUT"),
    )
    evolved = observe_position_opportunity(projection, later)
    assert evolved.position_id == projection.position_id
    assert evolved.original_opportunity_id == "opp:entry"
    assert evolved.correlated_opportunity_ids == ("opp:entry", "opp:window-2")
    assert len(evolved.current_strategy_memberships) == 2


def test_add_on_is_correlated_research_and_never_execution():
    projection, _ = opened_position()
    continuation = opportunity("opp:add-on", 20, membership("HIGHER_LOW_CONTINUATION"))
    candidate = add_on_research_candidate(
        projection, continuation, "HIGHER_LOW_CONTINUATION",
        observed_quantity=Decimal("100"),
        observed_existing_risk=Decimal("50"),
        observed_current_stop=Decimal("10.50"),
        observed_unrealized_r=Decimal("1.2"),
    )
    assert candidate.position_id == projection.position_id
    assert candidate.original_opportunity_id == projection.original_opportunity_id
    assert candidate.opportunity_id == "opp:add-on"
    assert candidate.research_only and not candidate.execution_authorized


def test_position_learning_separates_entry_from_transition_sequence():
    projection, _ = opened_position()
    projection = observe_position_strategies(
        projection, (membership("FIRST_PULLBACK"),),
        decision_cutoff=T0 + timedelta(minutes=4), opportunity_id="opp:pb",
    )
    projection = observe_position_strategies(
        projection, (membership("HIGHER_LOW_CONTINUATION"),),
        decision_cutoff=T0 + timedelta(minutes=7), opportunity_id="opp:hl",
    )
    features = dict(position_learning_features(projection))
    assert features["position_entry_strategy"] == "HIGH_OF_DAY_BREAKOUT"
    assert features["position_join_sequence"] == "FIRST_PULLBACK->HIGHER_LOW_CONTINUATION"
    edges = strategy_transition_edges(projection)
    assert ("HIGH_OF_DAY_BREAKOUT", "FIRST_PULLBACK", T0 + timedelta(minutes=4)) in edges
    assert ("FIRST_PULLBACK", "HIGHER_LOW_CONTINUATION", T0 + timedelta(minutes=7)) in edges


def test_transition_history_is_deterministic_and_idempotent():
    projection, _ = opened_position()
    values = (membership("FIRST_PULLBACK"), membership("HIGHER_LOW_CONTINUATION"))
    first = observe_position_strategies(
        projection, values, decision_cutoff=T0 + timedelta(minutes=5), opportunity_id="opp:2",
    )
    repeated = observe_position_strategies(
        first, values, decision_cutoff=T0 + timedelta(minutes=5), opportunity_id="opp:2",
    )
    assert repeated.strategy_transition_history == first.strategy_transition_history
    assert len({item.transition_id for item in repeated.strategy_transition_history}) == len(repeated.strategy_transition_history)
