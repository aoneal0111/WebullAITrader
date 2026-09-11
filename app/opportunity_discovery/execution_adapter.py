"""Explicit, fail-closed bridge from research opportunities to live setup shape.

The discovery taxonomy remains research-only.  This module does not authorize
orders; it validates the additional evidence required before a future caller
may hand one normalized opportunity to the existing Warrior execution path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Mapping

from app.strategies.warrior_momentum.models import SetupDetection, SetupState, SetupType, StopModel

from .contracts import DetectionState, DetectorAvailability, NormalizedOpportunity
from .taxonomy import STRATEGY_TAXONOMY


class AdapterRejection(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    NOT_EXECUTION_ALLOWLISTED = "NOT_EXECUTION_ALLOWLISTED"
    MISSING_TRIGGER = "MISSING_TRIGGER"
    MISSING_STRUCTURAL_STOP = "MISSING_STRUCTURAL_STOP"
    NON_POSITIVE_RISK = "NON_POSITIVE_RISK"
    MISSING_INVALIDATION = "MISSING_INVALIDATION"
    STALE_CONTEXT = "STALE_CONTEXT"
    MISSING_SETUP_QUALITY = "MISSING_SETUP_QUALITY"
    MISSING_EXECUTION_CONTEXT = "MISSING_EXECUTION_CONTEXT"
    FORMING_NOT_TRIGGERED = "FORMING_NOT_TRIGGERED"
    INVALIDATED = "INVALIDATED"
    MISSING_STOP = "MISSING_STOP"
    SPREAD = "SPREAD"
    LIQUIDITY = "LIQUIDITY"
    DUPLICATE_OPPORTUNITY = "DUPLICATE_OPPORTUNITY"
    LOWER_PRIORITY_OVERLAP = "LOWER_PRIORITY_OVERLAP"


@dataclass(frozen=True, slots=True)
class ExecutionCandidate:
    """Validated setup geometry ready for an existing execution seam."""

    strategy_type: str
    symbol: str
    opportunity_id: str
    opportunity_anchor: str
    detected_at: datetime
    formation_state: DetectionState
    trigger_price: Decimal
    structural_stop: Decimal
    risk_per_share: Decimal
    setup_quality: Decimal
    invalidation_state: DetectionState
    invalidation_reason: tuple[str, ...]
    freshness_authority: str
    context_age: timedelta
    spread_percent: Decimal
    dollar_volume: Decimal
    strategy_memberships: tuple[str, ...]
    selected_execution_strategy: str
    suppressed_duplicate_strategies: tuple[str, ...]
    selection_score: Decimal
    selection_priority: int
    execution_identity: str

    def __post_init__(self) -> None:
        if self.formation_state not in {DetectionState.DETECTED, DetectionState.STRENGTHENING}:
            raise ValueError("execution candidate must be an active detection")
        if self.trigger_price <= self.structural_stop or self.risk_per_share <= 0:
            raise ValueError("execution candidate requires positive structural risk")
        if self.invalidation_state is DetectionState.INVALIDATED:
            raise ValueError("invalidated candidate cannot be executable")
        if not self.freshness_authority.strip() or self.context_age < timedelta(0):
            raise ValueError("execution candidate requires valid freshness authority")

    def as_warrior_setup(self) -> SetupDetection:
        """Project validated geometry into the existing Warrior setup shape."""
        setup_types = {
            "MICRO_PULLBACK": (SetupType.MICRO_PULLBACK, StopModel.MICRO_PULLBACK_LOW),
            "FIRST_PULLBACK": (SetupType.MICRO_PULLBACK, StopModel.MICRO_PULLBACK_LOW),
            "HIGHER_LOW_CONTINUATION": (SetupType.MICRO_PULLBACK, StopModel.MICRO_PULLBACK_LOW),
            "SHALLOW_PULLBACK_CONTINUATION": (SetupType.MICRO_PULLBACK, StopModel.MICRO_PULLBACK_LOW),
            "VOLUME_CONTRACTION_PULLBACK": (SetupType.MICRO_PULLBACK, StopModel.MICRO_PULLBACK_LOW),
            "MOMENTUM_REACCELERATION": (SetupType.MICRO_PULLBACK, StopModel.MICRO_PULLBACK_LOW),
            "DEEP_PULLBACK_RECLAIM": (SetupType.MICRO_PULLBACK, StopModel.MICRO_PULLBACK_LOW),
            "HIGH_OF_DAY_BREAKOUT": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "FLAT_TOP_BREAKOUT": (SetupType.FLAT_TOP_BREAKOUT, StopModel.BREAKOUT_LEVEL),
            "CONSOLIDATION_BREAKOUT": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "ASCENDING_BASE_BREAKOUT": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "RANGE_COMPRESSION_BREAKOUT": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "BREAKOUT_RETEST_CONTINUATION": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "OPENING_RANGE_BREAKOUT": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "PREMARKET_HIGH_BREAKOUT": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "PREMARKET_CONSOLIDATION_BREAKOUT": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "OPENING_DRIVE_CONTINUATION": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "FAILED_BREAKOUT_RECLAIM": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "HOD_RECLAIM": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "GAP_AND_GO_CONTINUATION": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
            "POST_GAP_RECLAIM": (SetupType.MICRO_PULLBACK, StopModel.MICRO_PULLBACK_LOW),
            "DIP_AND_RIP": (SetupType.MICRO_PULLBACK, StopModel.MICRO_PULLBACK_LOW),
            "MOMENTUM_SQUEEZE_EXPANSION": (SetupType.HIGH_OF_DAY_BREAKOUT, StopModel.RECENT_SWING_LOW),
        }
        setup_type, stop_model = setup_types.get(self.strategy_type, (None, None))
        if setup_type is None:
            raise ValueError("strategy has no Warrior setup projection")
        state = SetupState.TRIGGERED if self.formation_state is DetectionState.DETECTED else SetupState.FORMING
        return SetupDetection(setup_type, state, self.setup_quality, self.trigger_price,
                              self.structural_stop, stop_model)


@dataclass(frozen=True, slots=True)
class AdapterResult:
    candidate: ExecutionCandidate | None
    rejection_reason: AdapterRejection | None = None
    suppressed_duplicate_strategies: tuple[str, ...] = ()


PULLBACK_CONTINUATION_FAMILY = frozenset({
    "MICRO_PULLBACK", "FIRST_PULLBACK", "HIGHER_LOW_CONTINUATION",
    "SHALLOW_PULLBACK_CONTINUATION", "VOLUME_CONTRACTION_PULLBACK",
    "MOMENTUM_REACCELERATION",
})
PULLBACK_CONTINUATION_ORDER = (
    "MICRO_PULLBACK", "FIRST_PULLBACK", "HIGHER_LOW_CONTINUATION",
    "SHALLOW_PULLBACK_CONTINUATION", "VOLUME_CONTRACTION_PULLBACK",
    "MOMENTUM_REACCELERATION",
)
ACTIVE_STRATEGY_ORDER = tuple(
    item.strategy_id for item in STRATEGY_TAXONOMY
    if item.availability is DetectorAvailability.ACTIVE
)
FULL_EXECUTION_ALLOWLIST = frozenset(ACTIVE_STRATEGY_ORDER)
FULL_INVALIDATION_CAPABILITIES = FULL_EXECUTION_ALLOWLIST
PHASE1_EXECUTION_ALLOWLIST = frozenset({"MICRO_PULLBACK"})
PHASE1_INVALIDATION_CAPABILITIES = frozenset({"MICRO_PULLBACK"})
PHASE2_EXECUTION_ALLOWLIST = PULLBACK_CONTINUATION_FAMILY
PHASE2_INVALIDATION_CAPABILITIES = PULLBACK_CONTINUATION_FAMILY


class MultiStrategyExecutionAdapter:
    """Bounded validator/selector; it never places or authorizes an order."""

    def __init__(self, *, execution_allowlist: frozenset[str] | set[str] | tuple[str, ...] = FULL_EXECUTION_ALLOWLIST,
                 invalidation_capabilities: frozenset[str] | set[str] | tuple[str, ...] = FULL_INVALIDATION_CAPABILITIES,
                 maximum_identities: int = 256, diagnostics: object | None = None) -> None:
        if maximum_identities <= 0:
            raise ValueError("maximum execution identities must be positive")
        self.execution_allowlist = frozenset(str(item).strip().upper() for item in execution_allowlist)
        self.invalidation_capabilities = frozenset(str(item).strip().upper() for item in invalidation_capabilities)
        self.maximum_identities = maximum_identities
        self.diagnostics = diagnostics
        self._seen_identities: dict[str, None] = {}

    def adapt(self, opportunity: NormalizedOpportunity, *, strategy_scores: Mapping[str, Decimal],
              observed_at: datetime, freshness_max_age: timedelta, freshness_authority: str,
              spread_percent: Decimal | None, dollar_volume: Decimal | None,
              diagnostics: object | None = None) -> AdapterResult:
        """Validate one normalized opportunity without changing its source state."""
        memberships = tuple(opportunity.memberships)
        matched = tuple(item for item in memberships if item.strategy_id in self.execution_allowlist)
        diagnostics = diagnostics if diagnostics is not None else self.diagnostics
        evaluations: list[dict[str, object]] = [
            {
                "strategy": item.strategy_id,
                "matched": False,
                "formation_state": item.state,
                "trigger_available": item.trigger_level is not None,
                "stop_available": item.structural_stop is not None,
                "invalidation_state": item.state,
                "selection_score": strategy_scores.get(item.strategy_id),
                "selected": False,
                "suppressed": False,
                "rejection_reason": AdapterRejection.NOT_EXECUTION_ALLOWLISTED,
            }
            for item in memberships
            if item.strategy_id not in self.execution_allowlist
        ]
        if not matched:
            return self._result(opportunity, AdapterRejection.NOT_EXECUTION_ALLOWLISTED, diagnostics,
                                strategies_evaluated=tuple(item.strategy_id for item in memberships),
                                strategies_matched=(), adapter_rejection_reason=AdapterRejection.NOT_EXECUTION_ALLOWLISTED,
                                strategy_evaluations=tuple(evaluations))

        age = observed_at - opportunity.decision_cutoff
        if age < timedelta(0) or age > freshness_max_age:
            return self._result(opportunity, AdapterRejection.STALE_CONTEXT, diagnostics,
                                strategies_evaluated=tuple(item.strategy_id for item in memberships),
                                strategies_matched=tuple(item.strategy_id for item in matched),
                                adapter_rejection_reason=AdapterRejection.STALE_CONTEXT,
                                strategy_evaluations=tuple(evaluations))
        if not freshness_authority.strip() or spread_percent is None or dollar_volume is None:
            return self._result(opportunity, AdapterRejection.MISSING_EXECUTION_CONTEXT, diagnostics,
                                strategies_evaluated=tuple(item.strategy_id for item in memberships),
                                strategies_matched=tuple(item.strategy_id for item in matched),
                                adapter_rejection_reason=AdapterRejection.MISSING_EXECUTION_CONTEXT,
                                strategy_evaluations=tuple(evaluations))

        eligible = []
        rejection = AdapterRejection.MISSING_INVALIDATION
        for item in matched:
            evaluation: dict[str, object] = {
                "strategy": item.strategy_id,
                "matched": True,
                "formation_state": item.state,
                "trigger_available": item.trigger_level is not None,
                "stop_available": item.structural_stop is not None,
                "invalidation_state": item.state,
                "selection_score": strategy_scores.get(item.strategy_id),
                "selected": False,
                "suppressed": False,
            }
            if item.strategy_id not in self.invalidation_capabilities:
                rejection = AdapterRejection.MISSING_INVALIDATION
                evaluation["rejection_reason"] = rejection
                evaluations.append(evaluation)
                continue
            if item.state is DetectionState.INVALIDATED:
                rejection = AdapterRejection.INVALIDATED
                evaluation["rejection_reason"] = rejection
                evaluations.append(evaluation)
                continue
            if item.trigger_level is None:
                rejection = AdapterRejection.MISSING_TRIGGER
                evaluation["rejection_reason"] = rejection
                evaluations.append(evaluation)
                continue
            if item.structural_stop is None:
                rejection = AdapterRejection.MISSING_STOP
                evaluation["rejection_reason"] = rejection
                evaluations.append(evaluation)
                continue
            if item.trigger_level <= item.structural_stop:
                rejection = AdapterRejection.NON_POSITIVE_RISK
                evaluation["rejection_reason"] = rejection
                evaluations.append(evaluation)
                continue
            if item.state not in {DetectionState.DETECTED, DetectionState.STRENGTHENING}:
                rejection = AdapterRejection.FORMING_NOT_TRIGGERED
                evaluation["rejection_reason"] = rejection
                evaluations.append(evaluation)
                continue
            score = strategy_scores.get(item.strategy_id)
            if score is None:
                rejection = AdapterRejection.MISSING_SETUP_QUALITY
                evaluation["rejection_reason"] = rejection
                evaluations.append(evaluation)
                continue
            eligible.append((item, score))
            evaluations.append(evaluation)

        if not eligible:
            return self._result(opportunity, rejection, diagnostics,
                                strategies_evaluated=tuple(item.strategy_id for item in memberships),
                                strategies_matched=tuple(item.strategy_id for item in matched),
                                adapter_rejection_reason=rejection,
                                strategy_evaluations=tuple(evaluations))

        primary = opportunity.primary_strategy_id
        strategy_order = {name: -index for index, name in enumerate(ACTIVE_STRATEGY_ORDER)}
        selected, selected_score = max(
            eligible,
            key=lambda pair: (
                2 if pair[0].state is DetectionState.DETECTED else 1,
                pair[1],
                strategy_order.get(pair[0].strategy_id, -len(strategy_order)),
                1 if pair[0].strategy_id == primary else 0,
            ),
        )
        execution_identity = self._identity(opportunity)
        if execution_identity in self._seen_identities:
            return self._result(opportunity, AdapterRejection.DUPLICATE_OPPORTUNITY, diagnostics,
                                strategies_evaluated=tuple(item.strategy_id for item in memberships),
                                strategies_matched=tuple(item.strategy_id for item in matched),
                                selected_execution_strategy=selected.strategy_id,
                                opportunity_anchor=opportunity.structural_anchor,
                                execution_identity=execution_identity,
                                adapter_rejection_reason=AdapterRejection.DUPLICATE_OPPORTUNITY)
        self._seen_identities[execution_identity] = None
        while len(self._seen_identities) > self.maximum_identities:
            self._seen_identities.pop(next(iter(self._seen_identities)))

        suppressed = tuple(item.strategy_id for item in matched if item.strategy_id != selected.strategy_id)
        for evaluation in evaluations:
            if evaluation["strategy"] == selected.strategy_id:
                evaluation["selected"] = True
            elif evaluation["matched"] and evaluation["strategy"] in suppressed:
                evaluation["suppressed"] = True
                evaluation["rejection_reason"] = AdapterRejection.LOWER_PRIORITY_OVERLAP
        candidate = ExecutionCandidate(
            strategy_type=selected.strategy_id,
            symbol=opportunity.symbol.upper(),
            opportunity_id=opportunity.opportunity_id,
            opportunity_anchor=opportunity.structural_anchor,
            detected_at=opportunity.decision_cutoff,
            formation_state=selected.state,
            trigger_price=selected.trigger_level,
            structural_stop=selected.structural_stop,
            risk_per_share=selected.trigger_level - selected.structural_stop,
            setup_quality=selected_score,
            invalidation_state=selected.state,
            invalidation_reason=selected.reason_codes,
            freshness_authority=freshness_authority,
            context_age=age,
            spread_percent=spread_percent,
            dollar_volume=dollar_volume,
            strategy_memberships=tuple(item.strategy_id for item in memberships),
            selected_execution_strategy=selected.strategy_id,
            suppressed_duplicate_strategies=suppressed,
            selection_score=selected_score,
            selection_priority=2 if selected.state is DetectionState.DETECTED else 1,
            execution_identity=execution_identity,
        )
        self._record(diagnostics, strategies_evaluated=tuple(item.strategy_id for item in memberships),
                     strategies_matched=tuple(item.strategy_id for item in matched),
                     selected_execution_strategy=selected.strategy_id,
                     suppressed_duplicate_strategies=suppressed,
                     opportunity_anchor=opportunity.structural_anchor,
                     execution_identity=execution_identity,
                     selection_score=selected_score, selection_priority=candidate.selection_priority,
                     strategy_evaluations=tuple(evaluations))
        return AdapterResult(candidate)

    def _result(self, opportunity, reason, diagnostics, **values) -> AdapterResult:
        self._record(diagnostics, **values)
        return AdapterResult(None, reason)

    @staticmethod
    def _identity(opportunity: NormalizedOpportunity) -> str:
        material = "|".join((opportunity.symbol.upper(), str(opportunity.session_date),
                             opportunity.session, opportunity.structural_anchor))
        return sha256(("ATLAS_EXECUTION_EPISODE_V1|" + material).encode()).hexdigest()

    @staticmethod
    def _record(diagnostics: object | None, **values: object) -> None:
        if diagnostics is None:
            return
        try:
            recorder = getattr(diagnostics, "record_strategy_selection", None)
            if recorder is not None:
                recorder(**values)
        except Exception:
            return


__all__ = [
    "AdapterRejection", "AdapterResult", "ExecutionCandidate",
    "MultiStrategyExecutionAdapter", "PHASE1_EXECUTION_ALLOWLIST",
    "PHASE1_INVALIDATION_CAPABILITIES", "PHASE2_EXECUTION_ALLOWLIST",
    "PHASE2_INVALIDATION_CAPABILITIES", "PULLBACK_CONTINUATION_FAMILY",
    "PULLBACK_CONTINUATION_ORDER", "ACTIVE_STRATEGY_ORDER",
    "FULL_EXECUTION_ALLOWLIST", "FULL_INVALIDATION_CAPABILITIES",
]
