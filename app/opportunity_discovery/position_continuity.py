"""Research-only continuity over authoritative positions and changing structures."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import IntEnum, StrEnum
from hashlib import sha256

from .contracts import DetectionState, NormalizedOpportunity, StrategyMembership


class PositionFocusTier(IntEnum):
    OPEN_POSITION = 1
    WORKING_ORDER = 2
    TRIGGERED_OPPORTUNITY = 3
    FORMING_OPPORTUNITY = 4
    SCANNER_DISCOVERY = 5


class StrategyTransitionType(StrEnum):
    STRATEGY_JOINED = "STRATEGY_JOINED"
    STRATEGY_STRENGTHENED = "STRATEGY_STRENGTHENED"
    STRATEGY_WEAKENED = "STRATEGY_WEAKENED"
    STRATEGY_LEFT = "STRATEGY_LEFT"
    STRATEGY_INVALIDATED = "STRATEGY_INVALIDATED"


class PositionThesisState(StrEnum):
    THESIS_INTACT = "THESIS_INTACT"
    THESIS_STRENGTHENING = "THESIS_STRENGTHENING"
    THESIS_WEAKENING = "THESIS_WEAKENING"
    THESIS_TRANSITIONING = "THESIS_TRANSITIONING"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"


@dataclass(frozen=True, slots=True)
class AuthoritativePositionReference:
    """Opaque reference to execution-owned position state; never competing truth."""

    source: str
    account_id: str
    position_key: str
    symbol: str
    lifecycle_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("source", "account_id", "position_key", "symbol"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")


@dataclass(frozen=True, slots=True)
class StrategyTransition:
    position_id: str
    original_opportunity_id: str
    opportunity_id: str | None
    transition_type: StrategyTransitionType
    strategy_id: str
    from_strategy_id: str | None
    to_strategy_id: str | None
    from_state: DetectionState | None
    to_state: DetectionState | None
    transition_timestamp: datetime
    decision_cutoff: datetime
    from_detector_version: str | None
    to_detector_version: str | None
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only:
            raise ValueError("strategy transitions are research-only")
        if self.transition_timestamp.tzinfo is None or self.decision_cutoff.tzinfo is None:
            raise ValueError("transition timestamps must be timezone-aware")
        if self.transition_timestamp != self.decision_cutoff:
            raise ValueError("transition time must preserve its decision cutoff")

    @property
    def transition_id(self) -> str:
        material = "|".join((
            self.position_id, self.original_opportunity_id,
            self.opportunity_id or "", self.transition_type.value,
            self.strategy_id, self.transition_timestamp.isoformat(),
            self.from_detector_version or "", self.to_detector_version or "",
        ))
        return sha256(material.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PositionResearchProjection:
    """Immutable observation correlated to, but never owning, a real position."""

    position_id: str
    symbol: str
    authoritative_position_reference: AuthoritativePositionReference
    original_opportunity_id: str
    entry_strategy_id: str
    entry_strategy_version: str
    entry_timestamp: datetime
    entry_price: Decimal
    initial_structural_stop: Decimal | None
    initial_risk: Decimal | None
    current_strategy_memberships: tuple[StrategyMembership, ...]
    strategy_transition_history: tuple[StrategyTransition, ...]
    current_thesis_state: PositionThesisState
    correlated_opportunity_ids: tuple[str, ...]
    position_open: bool = True
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only:
            raise ValueError("position projection must remain research-only")
        if not all((self.position_id.strip(), self.symbol.strip(), self.original_opportunity_id.strip(),
                    self.entry_strategy_id.strip(), self.entry_strategy_version.strip())):
            raise ValueError("position and immutable entry identity are required")
        if self.entry_timestamp.tzinfo is None or self.entry_price <= 0:
            raise ValueError("valid entry timestamp and price are required")
        if self.initial_structural_stop is not None and self.initial_structural_stop <= 0:
            raise ValueError("initial structural stop must be positive")
        if self.initial_risk is not None and self.initial_risk <= 0:
            raise ValueError("initial risk must be positive")
        identities = [item.strategy_id for item in self.current_strategy_memberships]
        if len(identities) != len(set(identities)):
            raise ValueError("current strategy memberships must be unique")
        if not self.correlated_opportunity_ids or self.correlated_opportunity_ids[0] != self.original_opportunity_id:
            raise ValueError("original opportunity must remain the first correlation")


@dataclass(frozen=True, slots=True)
class ResearchFocusSubject:
    subject_id: str
    symbol: str
    tier: PositionFocusTier

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.symbol.strip():
            raise ValueError("focus identity is required")


@dataclass(frozen=True, slots=True)
class AddOnResearchCandidate:
    position_id: str
    original_opportunity_id: str
    opportunity_id: str
    strategy_membership: StrategyMembership
    structural_reference: Decimal | None
    observed_quantity: Decimal | None = None
    observed_existing_risk: Decimal | None = None
    observed_current_stop: Decimal | None = None
    observed_unrealized_r: Decimal | None = None
    observed_realized_r: Decimal | None = None
    research_only: bool = True
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if not self.research_only or self.execution_authorized:
            raise ValueError("add-on candidate has no execution authority")
        if not all((self.position_id.strip(), self.original_opportunity_id.strip(), self.opportunity_id.strip())):
            raise ValueError("add-on research correlation is required")


def correlate_position(
    *, position_id: str, authoritative_reference: AuthoritativePositionReference,
    opportunity: NormalizedOpportunity, entry_strategy_id: str,
    entry_strategy_version: str, entry_timestamp: datetime, entry_price: Decimal,
    initial_structural_stop: Decimal | None = None, initial_risk: Decimal | None = None,
) -> PositionResearchProjection:
    """Create one research correlation from an execution-owned position identity."""

    if opportunity.symbol.upper() != authoritative_reference.symbol.strip().upper():
        raise ValueError("authoritative position reference must identify the opportunity symbol")
    if not any(item.strategy_id == entry_strategy_id for item in opportunity.memberships):
        raise ValueError("entry strategy must belong to the original opportunity")
    return PositionResearchProjection(
        position_id, opportunity.symbol.upper(), authoritative_reference,
        opportunity.opportunity_id, entry_strategy_id, entry_strategy_version,
        entry_timestamp, entry_price, initial_structural_stop, initial_risk,
        opportunity.memberships, (), PositionThesisState.THESIS_INTACT,
        (opportunity.opportunity_id,), True, True,
    )


def observe_position_strategies(
    projection: PositionResearchProjection,
    memberships: tuple[StrategyMembership, ...],
    *, decision_cutoff: datetime, opportunity_id: str | None = None,
) -> PositionResearchProjection:
    """Append structural observations without changing position ownership or entry facts."""

    if decision_cutoff.tzinfo is None:
        raise ValueError("decision cutoff must be timezone-aware")
    prior = {item.strategy_id: item for item in projection.current_strategy_memberships}
    current = {item.strategy_id: item for item in memberships}
    if len(current) != len(memberships):
        raise ValueError("observed memberships must be unique")
    transitions: list[StrategyTransition] = []
    prior_primary = next(iter(sorted(prior)), projection.entry_strategy_id)
    current_primary = next(iter(sorted(current)), None)

    for strategy_id in sorted(current.keys() - prior.keys()):
        item = current[strategy_id]
        transitions.append(_transition(
            projection, opportunity_id, StrategyTransitionType.STRATEGY_JOINED,
            strategy_id, prior_primary, strategy_id, None, item.state,
            decision_cutoff, None, item.strategy_version,
        ))
    for strategy_id in sorted(prior.keys() - current.keys()):
        item = prior[strategy_id]
        transition_type = (
            StrategyTransitionType.STRATEGY_INVALIDATED
            if item.state is DetectionState.INVALIDATED
            else StrategyTransitionType.STRATEGY_LEFT
        )
        transitions.append(_transition(
            projection, opportunity_id, transition_type, strategy_id,
            strategy_id, current_primary, item.state, None, decision_cutoff,
            item.strategy_version, None,
        ))
    for strategy_id in sorted(prior.keys() & current.keys()):
        before, after = prior[strategy_id], current[strategy_id]
        transition_type = _state_transition(before.state, after.state)
        if transition_type is not None:
            transitions.append(_transition(
                projection, opportunity_id, transition_type, strategy_id,
                strategy_id, strategy_id, before.state, after.state,
                decision_cutoff, before.strategy_version, after.strategy_version,
            ))

    seen = {item.transition_id for item in projection.strategy_transition_history}
    appended = tuple(item for item in transitions if item.transition_id not in seen)
    correlated = projection.correlated_opportunity_ids
    if opportunity_id is not None and opportunity_id not in correlated:
        correlated = (*correlated, opportunity_id)
    thesis = _thesis_state(appended)
    return replace(
        projection,
        current_strategy_memberships=tuple(current[key] for key in sorted(current)),
        strategy_transition_history=(*projection.strategy_transition_history, *appended),
        current_thesis_state=thesis,
        correlated_opportunity_ids=correlated,
    )


def observe_position_opportunity(
    projection: PositionResearchProjection,
    opportunity: NormalizedOpportunity,
) -> PositionResearchProjection:
    if opportunity.symbol.upper() != projection.symbol:
        raise ValueError("opportunity symbol does not match position")
    return observe_position_strategies(
        projection, opportunity.memberships,
        decision_cutoff=opportunity.decision_cutoff,
        opportunity_id=opportunity.opportunity_id,
    )


def prioritize_research_focus(
    subjects: tuple[ResearchFocusSubject, ...],
) -> tuple[ResearchFocusSubject, ...]:
    """Keep one highest-priority research subject per symbol."""

    selected: dict[str, ResearchFocusSubject] = {}
    for item in subjects:
        symbol = item.symbol.strip().upper()
        previous = selected.get(symbol)
        if previous is None or (item.tier, item.subject_id) < (previous.tier, previous.subject_id):
            selected[symbol] = replace(item, symbol=symbol)
    return tuple(sorted(selected.values(), key=lambda item: (item.tier, item.symbol, item.subject_id)))


def add_on_research_candidate(
    projection: PositionResearchProjection,
    opportunity: NormalizedOpportunity,
    strategy_id: str,
    **observed_authoritative_values: Decimal | None,
) -> AddOnResearchCandidate:
    if not projection.position_open or opportunity.symbol.upper() != projection.symbol:
        raise ValueError("add-on research requires a matching open position")
    membership = next((item for item in opportunity.memberships if item.strategy_id == strategy_id), None)
    if membership is None:
        raise ValueError("add-on strategy must belong to the observed opportunity")
    return AddOnResearchCandidate(
        projection.position_id, projection.original_opportunity_id,
        opportunity.opportunity_id, membership, membership.reference_price,
        **observed_authoritative_values,
    )


def position_learning_features(projection: PositionResearchProjection) -> tuple[tuple[str, object], ...]:
    joined = tuple(
        item.strategy_id for item in projection.strategy_transition_history
        if item.transition_type is StrategyTransitionType.STRATEGY_JOINED
    )
    current = tuple(item.strategy_id for item in projection.current_strategy_memberships)
    return (
        ("position_entry_strategy", projection.entry_strategy_id),
        ("position_current_strategies", "+".join(current)),
        ("position_join_sequence", "->".join(joined)),
        ("position_thesis_state", projection.current_thesis_state.value),
    )


def strategy_transition_edges(
    projection: PositionResearchProjection,
) -> tuple[tuple[str, str, datetime], ...]:
    """Expose only actually observed cross-strategy edges for explainable analysis."""

    return tuple(
        (item.from_strategy_id, item.to_strategy_id, item.transition_timestamp)
        for item in projection.strategy_transition_history
        if item.from_strategy_id is not None
        and item.to_strategy_id is not None
        and item.from_strategy_id != item.to_strategy_id
    )


def _state_transition(before: DetectionState, after: DetectionState) -> StrategyTransitionType | None:
    if before is after:
        return None
    if after is DetectionState.INVALIDATED:
        return StrategyTransitionType.STRATEGY_INVALIDATED
    if after is DetectionState.STRENGTHENING:
        return StrategyTransitionType.STRATEGY_STRENGTHENED
    if after in {DetectionState.WEAKENING, DetectionState.FORMING, DetectionState.NOT_DETECTED}:
        return StrategyTransitionType.STRATEGY_WEAKENED
    return StrategyTransitionType.STRATEGY_JOINED


def _thesis_state(transitions: tuple[StrategyTransition, ...]) -> PositionThesisState:
    kinds = {item.transition_type for item in transitions}
    if StrategyTransitionType.STRATEGY_INVALIDATED in kinds:
        return PositionThesisState.THESIS_INVALIDATED
    if StrategyTransitionType.STRATEGY_JOINED in kinds and StrategyTransitionType.STRATEGY_LEFT in kinds:
        return PositionThesisState.THESIS_TRANSITIONING
    if StrategyTransitionType.STRATEGY_STRENGTHENED in kinds or StrategyTransitionType.STRATEGY_JOINED in kinds:
        return PositionThesisState.THESIS_STRENGTHENING
    if StrategyTransitionType.STRATEGY_WEAKENED in kinds or StrategyTransitionType.STRATEGY_LEFT in kinds:
        return PositionThesisState.THESIS_WEAKENING
    return PositionThesisState.THESIS_INTACT


def _transition(
    projection, opportunity_id, transition_type, strategy_id,
    from_strategy_id, to_strategy_id, from_state, to_state,
    timestamp, from_version, to_version,
) -> StrategyTransition:
    return StrategyTransition(
        projection.position_id, projection.original_opportunity_id,
        opportunity_id, transition_type, strategy_id, from_strategy_id,
        to_strategy_id, from_state, to_state, timestamp, timestamp,
        from_version, to_version, True,
    )
