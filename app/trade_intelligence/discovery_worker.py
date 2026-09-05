"""Worker-thread discovery evaluation and append-only research persistence."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from hashlib import sha256

from app.opportunity_discovery import (
    DetectionState,
    MultiStrategyDiscoveryEngine,
    PositionThesisState,
    StrategyTransitionType,
)

from .discovery_runtime import (
    DiscoveryTelemetry, KnownDiscoveryContext,
    RuntimeDiscoveryObservation,
    StrategyCoverage,
)

_FIRING_STATES = {DetectionState.DETECTED, DetectionState.STRENGTHENING}
_ADD_ON_FAMILIES = {"CONTINUATION", "PULLBACK", "RECLAIM", "COMPRESSION_EXPANSION"}


@dataclass(slots=True)
class _PositionState:
    position_id: str
    original_opportunity_id: str
    entry_strategy_id: str | None
    memberships: dict[str, object]
    thesis_state: PositionThesisState
    opportunity_ids: set[str]
    correlated: bool


class DiscoveryWorker:
    """Single-threaded state owned by the existing Trade Intelligence worker."""

    def __init__(self, *, state_limit: int = 10_000) -> None:
        self.engine = MultiStrategyDiscoveryEngine(
            maximum_episodes=state_limit,
            maximum_opportunities=max(1, state_limit // 2),
        )
        self.state_limit = state_limit
        self._opportunity_signatures: OrderedDict[str, tuple[object, ...]] = OrderedDict()
        self._membership_signatures: OrderedDict[tuple[str, str], tuple[object, ...]] = OrderedDict()
        self._positions: OrderedDict[str, _PositionState] = OrderedDict()
        self._latest_contexts: OrderedDict[str, KnownDiscoveryContext] = OrderedDict()
        self._coverage = {
            item.definition.strategy_id: {
                "evaluations": 0, "raw": 0, "episodes": set(), "episode_total": 0,
                "opportunities": set(), "opportunity_total": 0,
            }
            for item in self.engine.registry.detectors
        }
        self._cycles = self._memberships = self._transitions = 0
        self._position_correlations = self._thesis = self._add_ons = 0

    def process(self, store, observation: RuntimeDiscoveryObservation) -> None:
        batch = self.engine.observe(observation.context)
        self._cycles += 1
        detected = tuple(item for item in batch.detections if item.state in {
            DetectionState.FORMING, DetectionState.DETECTED,
            DetectionState.STRENGTHENING, DetectionState.WEAKENING,
            DetectionState.INVALIDATED,
        })
        by_anchor = {}
        for item in batch.detections:
            coverage = self._coverage[item.strategy_id]
            coverage["evaluations"] += 1
            if item.state in _FIRING_STATES:
                coverage["raw"] += 1
                self._bounded_unique(coverage, "episodes", "episode_total", item.detector_episode_id)
                by_anchor.setdefault(item.opportunity_anchor, []).append(item)

        for opportunity in batch.opportunities:
            members = tuple(sorted(by_anchor.get(opportunity.structural_anchor, ()), key=lambda item: item.strategy_id))
            for item in members:
                self._bounded_unique(
                    self._coverage[item.strategy_id], "opportunities", "opportunity_total",
                    opportunity.opportunity_id,
                )
            signature = tuple((item.strategy_id, item.strategy_version, item.state.value,
                               item.detector_episode_id) for item in members)
            prior = self._opportunity_signatures.get(opportunity.opportunity_id)
            if prior != signature:
                payload = {
                    "observation_id": _identity("opportunity", opportunity.opportunity_id,
                                                observation.observed_at.isoformat(), signature),
                    "schema_version": observation.schema_version,
                    "opportunity_id": opportunity.opportunity_id,
                    "symbol": opportunity.symbol,
                    "session": opportunity.session,
                    "session_date": opportunity.session_date.isoformat(),
                    "decision_cutoff": opportunity.decision_cutoff.isoformat(),
                    "structural_window": opportunity.structural_anchor,
                    "primary_strategy": opportunity.primary_strategy_id,
                    "membership_count": len(members),
                    "complete_r_plan": opportunity.complete_r_plan,
                    "created_at": observation.observed_at.isoformat(),
                    "focus_tier": int(observation.focus_tier),
                    "research_only": True,
                }
                store.put_discovery_opportunity(payload)
                self._remember(self._opportunity_signatures, opportunity.opportunity_id, signature)
            for item in members:
                self._persist_membership(store, opportunity.opportunity_id, item)

        self._observe_position(store, observation, batch.opportunities, detected)
        self._remember_context(observation, batch.opportunities)

    def context_for(self, symbol: str) -> KnownDiscoveryContext | None:
        return self._latest_contexts.get(symbol.strip().upper())

    def _remember_context(self, observation, opportunities) -> None:
        symbol = observation.context.symbol.strip().upper()
        ordered = tuple(sorted(
            opportunities,
            key=lambda item: (
                "HIGH_OF_DAY_BREAKOUT" not in {
                    member.strategy_id for member in item.memberships
                },
                item.opportunity_id,
            ),
        ))
        selected = ordered[0] if ordered else None
        context = KnownDiscoveryContext(
            symbol=symbol,
            observed_at=observation.observed_at,
            opportunity_id=(
                None if selected is None else selected.opportunity_id
            ),
            detector_memberships=tuple(sorted({
                member.strategy_id
                for item in opportunities
                for member in item.memberships
            })),
        )
        self._latest_contexts[symbol] = context
        self._latest_contexts.move_to_end(symbol)
        while len(self._latest_contexts) > self.state_limit:
            self._latest_contexts.popitem(last=False)

    def telemetry(self, *, market_observations: int = 0, completed_bars: int = 0,
                  callback_percentiles: tuple[float, float, float, float] = (0, 0, 0, 0)) -> DiscoveryTelemetry:
        metrics = self.engine.metrics()
        coverage = tuple(StrategyCoverage(
            strategy_id, int(values["evaluations"]), int(values["raw"]),
            int(values["episode_total"]), int(values["opportunity_total"]),
        ) for strategy_id, values in sorted(self._coverage.items()))
        return DiscoveryTelemetry(
            market_observations=market_observations,
            completed_bars=completed_bars,
            discovery_cycles=self._cycles,
            detector_evaluations=metrics.detector_evaluations,
            raw_detector_firings=metrics.raw_detector_firings,
            unique_detector_episodes=metrics.unique_detector_episodes,
            normalized_opportunities=metrics.normalized_opportunities,
            strategy_memberships=self._memberships,
            strategy_transitions=self._transitions,
            position_correlations=self._position_correlations,
            thesis_observations=self._thesis,
            add_on_candidates=self._add_ons,
            callback_build_p50_ms=callback_percentiles[0],
            callback_build_p90_ms=callback_percentiles[1],
            callback_build_p99_ms=callback_percentiles[2],
            callback_build_max_ms=callback_percentiles[3],
            coverage=coverage,
        )

    def _persist_membership(self, store, opportunity_id, item) -> None:
        key = (opportunity_id, item.strategy_id)
        signature = (
            item.strategy_version, item.state.value, item.detector_episode_id,
            item.trigger_level, item.structural_stop, item.reason_codes,
            item.quality_components, item.missing_features,
        )
        if self._membership_signatures.get(key) == signature:
            return
        payload = {
            "observation_id": _identity("membership", opportunity_id, item.strategy_id,
                                        item.decision_cutoff.isoformat(), signature),
            "opportunity_id": opportunity_id,
            "strategy_id": item.strategy_id,
            "detector_version": item.strategy_version,
            "family": item.family.value,
            "state": item.state.value,
            "decision_cutoff": item.decision_cutoff.isoformat(),
            "detector_episode_id": item.detector_episode_id,
            "setup_anchor": item.setup_anchor,
            "reference_price": item.reference_price,
            "trigger_level": item.trigger_level,
            "structural_stop": item.structural_stop,
            "quality_components": item.quality_components,
            "required_features_observed": item.required_features_observed,
            "optional_features_observed": item.optional_features_observed,
            "missing_features": item.missing_features,
            "reason_codes": item.reason_codes,
            "research_only": True,
        }
        if store.put_strategy_membership(payload):
            self._memberships += 1
        self._remember(self._membership_signatures, key, signature)

    def _observe_position(self, store, observation, opportunities, detected) -> None:
        authoritative = observation.authoritative_position
        if authoritative is None:
            return
        position = self._positions.get(authoritative.position_id)
        opportunity = opportunities[0] if opportunities else None
        opportunity_id = None if opportunity is None else opportunity.opportunity_id
        new_position = position is None
        if new_position:
            original = authoritative.original_opportunity_id or opportunity_id or f"UNCORRELATED:{authoritative.position_id}"
            position = _PositionState(
                authoritative.position_id, original, authoritative.entry_strategy_id,
                {}, PositionThesisState.THESIS_INTACT, set(),
                authoritative.original_opportunity_id is not None or opportunity_id is not None,
            )
            self._positions[authoritative.position_id] = position
        current = {item.strategy_id: item for item in detected}
        transitions = self._position_transitions(position, current, opportunity_id, observation)
        for payload in transitions:
            if store.put_strategy_transition(payload):
                self._transitions += 1
        thesis = _thesis(tuple(item["transition_type"] for item in transitions), position.thesis_state)
        correlation_new = opportunity_id is not None and opportunity_id not in position.opportunity_ids
        if not position.opportunity_ids or correlation_new:
            payload = {
                "observation_id": _identity("position-correlation", position.position_id,
                                            opportunity_id or "NONE", observation.observed_at.isoformat()),
                "position_id": position.position_id,
                "authoritative_position_key": authoritative.position_key,
                "authoritative_source": authoritative.source,
                "account_id": authoritative.account_id,
                "symbol": authoritative.symbol,
                "quantity": authoritative.quantity,
                "average_entry_price": authoritative.average_entry_price,
                "opportunity_id": opportunity_id,
                "original_opportunity_id": position.original_opportunity_id,
                "decision_cutoff": observation.observed_at.isoformat(),
                "entry_strategy_id": position.entry_strategy_id,
                "entry_strategy_version": authoritative.entry_strategy_version,
                "entry_timestamp": authoritative.entry_timestamp,
                "entry_price": authoritative.entry_price,
                "correlation_status": "CORRELATED" if position.correlated else "UNCORRELATED",
                "research_only": True,
            }
            if store.put_position_correlation(payload):
                self._position_correlations += 1
        if opportunity_id is not None:
            position.opportunity_ids.add(opportunity_id)
        if thesis != position.thesis_state or new_position:
            payload = {
                "observation_id": _identity("thesis", position.position_id, thesis.value,
                                            observation.observed_at.isoformat()),
                "position_id": position.position_id,
                "thesis_state": thesis.value,
                "decision_cutoff": observation.observed_at.isoformat(),
                "transition_ids": tuple(item["transition_id"] for item in transitions),
                "research_only": True,
            }
            if store.put_position_thesis(payload):
                self._thesis += 1
        was_existing = bool(position.memberships)
        joined = current.keys() - position.memberships.keys()
        if was_existing and opportunity is not None:
            for strategy_id in sorted(joined):
                item = current[strategy_id]
                if item.family.value not in _ADD_ON_FAMILIES:
                    continue
                payload = {
                    "candidate_id": _identity("add-on", position.position_id, opportunity_id,
                                              strategy_id, observation.observed_at.isoformat()),
                    "position_id": position.position_id,
                    "original_opportunity_id": position.original_opportunity_id,
                    "opportunity_id": opportunity_id,
                    "strategy_id": strategy_id,
                    "detector_version": item.strategy_version,
                    "decision_cutoff": observation.observed_at.isoformat(),
                    "new_structural_reference": item.reference_price,
                    "observed_quantity": authoritative.quantity,
                    "observed_existing_risk": None,
                    "observed_current_stop": None,
                    "observed_unrealized_r": None,
                    "observed_realized_r": None,
                    "research_only": True,
                    "execution_authorized": False,
                }
                if store.put_add_on_candidate(payload):
                    self._add_ons += 1
        position.memberships = current
        position.thesis_state = thesis
        self._positions.move_to_end(position.position_id)
        while len(self._positions) > self.state_limit:
            self._positions.popitem(last=False)

    def _position_transitions(self, position, current, opportunity_id, observation):
        prior = position.memberships
        result = []
        for strategy_id in sorted(current.keys() | prior.keys()):
            before, after = prior.get(strategy_id), current.get(strategy_id)
            transition_type = None
            if before is None:
                transition_type = StrategyTransitionType.STRATEGY_JOINED
            elif after is None:
                transition_type = StrategyTransitionType.STRATEGY_LEFT
            elif before.state != after.state:
                if after.state is DetectionState.INVALIDATED:
                    transition_type = StrategyTransitionType.STRATEGY_INVALIDATED
                elif after.state is DetectionState.STRENGTHENING:
                    transition_type = StrategyTransitionType.STRATEGY_STRENGTHENED
                else:
                    transition_type = StrategyTransitionType.STRATEGY_WEAKENED
            if transition_type is None:
                continue
            payload = {
                "transition_id": _identity("transition", position.position_id,
                                           strategy_id, transition_type.value,
                                           observation.observed_at.isoformat()),
                "position_id": position.position_id,
                "original_opportunity_id": position.original_opportunity_id,
                "opportunity_id": opportunity_id,
                "strategy_id": strategy_id,
                "transition_type": transition_type.value,
                "transition_timestamp": observation.observed_at.isoformat(),
                "decision_cutoff": observation.context.decision_cutoff.isoformat(),
                "from_state": None if before is None else before.state.value,
                "to_state": None if after is None else after.state.value,
                "from_detector_version": None if before is None else before.strategy_version,
                "to_detector_version": None if after is None else after.strategy_version,
                "research_only": True,
            }
            result.append(payload)
        return tuple(result)

    def memory_metrics(self) -> dict[str, int]:
        return {"opportunity_symbol_count": len(self._latest_contexts),
                "membership_count": len(self._membership_signatures),
                "opportunity_state_count": len(self._opportunity_signatures),
                "position_state_count": len(self._positions)}

    def _remember(self, mapping: OrderedDict, key, value) -> None:
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > self.state_limit:
            mapping.popitem(last=False)

    def _bounded_unique(self, coverage, set_name, total_name, identity) -> None:
        values = coverage[set_name]
        if identity in values:
            return
        coverage[total_name] += 1
        values.add(identity)
        if len(values) > self.state_limit:
            values.pop()


def _thesis(transitions: tuple[str, ...], previous: PositionThesisState) -> PositionThesisState:
    kinds = set(transitions)
    if StrategyTransitionType.STRATEGY_INVALIDATED.value in kinds:
        return PositionThesisState.THESIS_INVALIDATED
    if (StrategyTransitionType.STRATEGY_JOINED.value in kinds
            and StrategyTransitionType.STRATEGY_LEFT.value in kinds):
        return PositionThesisState.THESIS_TRANSITIONING
    if StrategyTransitionType.STRATEGY_STRENGTHENED.value in kinds or StrategyTransitionType.STRATEGY_JOINED.value in kinds:
        return PositionThesisState.THESIS_STRENGTHENING
    if StrategyTransitionType.STRATEGY_WEAKENED.value in kinds or StrategyTransitionType.STRATEGY_LEFT.value in kinds:
        return PositionThesisState.THESIS_WEAKENING
    return previous


def _identity(*parts) -> str:
    return sha256("|".join(str(item) for item in parts).encode()).hexdigest()
