"""Fail-closed PAPER-only entry-timing treatment over DI-2 evidence."""

from __future__ import annotations

from dataclasses import dataclass, is_dataclass, replace
from decimal import Decimal
from typing import Any, Callable, Mapping

from app.paper_trade_experiment.harness import (
    ExperimentDefinition, ExperimentOpportunity, ExperimentRouter,
    PaperExperimentJournal, CONTROL_ARM, TREATMENT_ARM,
)
from app.strategies.warrior_momentum.autonomous_paper import lifecycle_identity

from .models import HistoricalIntelligenceResult


EXPERIMENT_VERSION = "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_V1"
PAPER_ONLY = "PAPER_ONLY"
OBSERVE_ONLY = "OBSERVE_ONLY"
PAPER_TREATMENT = "PAPER_TREATMENT"


@dataclass(frozen=True, slots=True)
class EntryIntelligenceConfig:
    enabled: bool = False
    mode: str = OBSERVE_ONLY
    environment: str = PAPER_ONLY
    allocation_percent: int = 50
    version: str = EXPERIMENT_VERSION
    journal_path: str | None = None


@dataclass(frozen=True, slots=True)
class SetupQualityAssessment:
    state: str
    supporting_factors: tuple[str, ...] = ()
    blocking_factors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntryQualityAssessment:
    state: str
    factors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PaperEntryIntelligenceDecision:
    opportunity_id: str | None
    timestamp: Any
    memberships: tuple[str, ...]
    primary_strategy: str | None
    arm: str
    control_decision: str
    treatment_decision: str
    setup_quality: SetupQualityAssessment
    entry_quality: EntryQualityAssessment
    recommended_entry_mode: str
    current_stage: str
    trigger: Decimal | None
    stop: Decimal | None
    current_price: Decimal | None
    supporting_reasons: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    di_version: str = "ATLAS_HISTORICAL_DECISION_INTELLIGENCE_DI2_V1"
    artifact_version: str = "ATLAS_HISTORICAL_DECISION_INTELLIGENCE_V1"
    authorizing_memberships: tuple[str, ...] = ()


def entry_experiment_definition(strategy: str) -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_id="historical_entry_timing",
        version=EXPERIMENT_VERSION,
        strategy=strategy,
        control_arm={"entry_mode": "CURRENT_TRIGGER", "research_only": True},
        treatment_arm={"entry_mode": "TRIGGER_READY_ENTRY", "research_only": True},
        start_timestamp="2026-01-01T00:00:00+00:00",
        enabled=True,
        paper_only=True,
        control_allocation_percent=50,
        treatment_allocation_percent=50,
        minimum_sample_target=300,
        rejection_criteria=("invalid geometry", "insufficient evidence", "chased entry"),
        promotion_review_criteria=("human review only", "no automatic promotion"),
        research_provenance="ATLAS_HISTORICAL_DECISION_INTELLIGENCE_V1",
        experimental_dimension="entry_timing",
    )


class HistoricalPaperEntryTimingPolicy:
    """Advisory DI-2 entry seam; it returns a normal signal or no override."""

    def __init__(self, *, config: EntryIntelligenceConfig | None = None,
                 journal: PaperExperimentJournal | None = None) -> None:
        self.config = config or EntryIntelligenceConfig()
        self._journal = journal
        self._owned_journal = False
        if self._journal is None and self.config.journal_path:
            self._journal = PaperExperimentJournal(self.config.journal_path)
            self._owned_journal = True
        self._router = None if self._journal is None else ExperimentRouter(
            self._journal, enabled=self.config.enabled,
        )

    def close(self) -> None:
        if self._owned_journal and self._journal is not None:
            self._journal.close()

    def assess(self, result: HistoricalIntelligenceResult | None, candidate: object,
               *, environment: str, signal_factory: Callable[[object], object | None],
               decision_timestamp: Any | None = None,
               existing_signal: object | None = None) -> tuple[PaperEntryIntelligenceDecision, object | None]:
        memberships = tuple(() if result is None else result.recognized_memberships)
        strategy = None if result is None else result.primary_strategy
        stage = "NO_SETUP" if result is None else result.setup_stage
        trigger = None if result is None else result.trigger_price
        stop = None if result is None else result.structural_stop
        price = None if result is None else result.current_price
        setup = self._setup_quality(result)
        entry = self._entry_quality(result)
        opportunity_id = None if result is None else result.opportunity_id
        timestamp = decision_timestamp if decision_timestamp is not None else (
            None if result is None else result.evaluated_at
        )
        base = PaperEntryIntelligenceDecision(
            opportunity_id=opportunity_id, timestamp=timestamp,
            memberships=memberships, primary_strategy=strategy, arm=CONTROL_ARM,
            control_decision="CURRENT_TRIGGER", treatment_decision="CONTROL",
            setup_quality=setup, entry_quality=entry,
            recommended_entry_mode="CURRENT_TRIGGER", current_stage=stage,
            trigger=trigger, stop=stop, current_price=price,
            blocking_reasons=setup.blocking_factors + entry.factors,
            limitations=() if result is None else result.limitations,
        )
        if (
            self._router is None or self.config.mode != PAPER_TREATMENT
            or str(environment).upper() != "PAPER"
            or result is None or strategy is None or opportunity_id is None
        ):
            return base, None
        definition = entry_experiment_definition(strategy)
        definition = replace(
            definition,
            control_allocation_percent=max(0, min(100, self.config.allocation_percent)),
            treatment_allocation_percent=100 - max(0, min(100, self.config.allocation_percent)),
            enabled=self.config.enabled,
        )
        self._journal.register(definition)
        opportunity = ExperimentOpportunity(
            assignment_identity=opportunity_id, strategy=strategy,
            symbol=str(getattr(candidate, "symbol", "")),
            trading_date=str(result.trading_date or ""),
            decision_timestamp=str(timestamp or ""),
            context={"stage": stage, "entry_quality": entry.state},
        )
        assignment = self._router.assign(definition, opportunity, mode="PAPER")
        treatment_ok = self._eligible(result, setup, entry, candidate)
        decision = replace(
            base, arm=assignment.arm,
            treatment_decision=("ELIGIBLE" if treatment_ok else "CONTROL_FALLBACK"),
            recommended_entry_mode=("TRIGGER_READY_ENTRY" if treatment_ok else "CURRENT_TRIGGER"),
            supporting_reasons=("EXPLICIT_TRIGGER_READY", "HISTORICAL_EVIDENCE_SUFFICIENT")
            if treatment_ok else (),
            authorizing_memberships=tuple(
                str(row.get("strategy")) for row in result.readiness_memberships
                if str(row.get("state")) == "TRIGGER_ARMED"
            ) if treatment_ok else (),
        )
        self._journal.record_decision(
            assignment.assignment_id,
            control_decision=decision.control_decision,
            treatment_decision=decision.treatment_decision,
            selected_mode=decision.recommended_entry_mode,
            decision={
                "opportunity_id": opportunity_id, "arm": assignment.arm,
                "setup_quality": setup.state, "entry_quality": entry.state,
                "stage": stage, "treatment_eligible": treatment_ok,
                "authorizing_memberships": decision.authorizing_memberships,
                "readiness_provenance": (
                    result.readiness_memberships if treatment_ok else ()
                ),
            },
        )
        if existing_signal is not None:
            # The signal is the authoritative normal-control decision.  Link
            # its existing lifecycle before recording the shadow so subsequent
            # PAPER order/fill events can be attributed without submitting a
            # counterfactual order.
            self._journal.link_lifecycle(
                assignment.assignment_id, lifecycle_identity(existing_signal),
            )
            self._journal.record_shadow(
                assignment.assignment_id, shadow_type="CONTROL_TRIGGER",
                observed_at=str(timestamp or ""), price=price, stage=stage,
                shadow={"control_signal_present": True},
            )
            self._journal.record_shadow(
                assignment.assignment_id, shadow_type="CONTROL_DECISION",
                observed_at=str(timestamp or ""), price=price, stage=stage,
                shadow={"control_signal_present": True},
            )
            return decision, None
        if assignment.arm == TREATMENT_ARM and treatment_ok:
            self._journal.record_shadow(
                assignment.assignment_id, shadow_type="CONTROL_PENDING",
                observed_at=str(timestamp or ""), price=price, stage=stage,
                shadow={"control_mode": "CURRENT_TRIGGER"},
            )
        if assignment.arm != TREATMENT_ARM or not treatment_ok:
            return decision, None
        try:
            setup_detection = getattr(candidate, "setup", None)
            if setup_detection is None:
                return replace(decision, treatment_decision="CONTROL_FALLBACK"), None
            projected_setup = replace(
                setup_detection, state=_triggered_state(setup_detection.state),
            )
            if is_dataclass(candidate):
                treatment_candidate = replace(candidate, setup=projected_setup)
            else:
                treatment_candidate = type(candidate)(
                    **{**vars(candidate), "setup": projected_setup}
                )
            treatment_signal = signal_factory(treatment_candidate)
            if treatment_signal is not None:
                self._journal.link_lifecycle(
                    assignment.assignment_id, lifecycle_identity(treatment_signal),
                )
            return decision, treatment_signal
        except Exception:
            return replace(decision, treatment_decision="CONTROL_FALLBACK"), None

    def observe_paper_event(self, event: object) -> None:
        """Link authoritative PAPER events to an existing assignment only."""
        if self._journal is None:
            return
        try:
            order = getattr(event, "order", None)
            request = getattr(order, "request", None)
            lifecycle = (
                None if request is None else getattr(request, "strategy_lifecycle_id", None)
            ) or getattr(order, "lifecycle_id", None)
            if not lifecycle:
                return
            assignment = self._journal.assignment_for_lifecycle(str(lifecycle))
            if assignment is None:
                return
            fill = getattr(event, "fill", None)
            raw_type = str(getattr(event, "event_type", ""))
            event_type = {
                "ORDER_ACCEPTED": "ORDER_SUBMITTED",
                "ORDER_WORKING": "ORDER_SUBMITTED",
                "ORDER_FILLED": "FILLED",
                "ORDER_PARTIALLY_FILLED": "FILLED",
            }.get(raw_type, raw_type)
            if event_type not in {"ORDER_SUBMITTED", "ORDER_REPLACED", "ORDER_CANCELLED",
                                  "ORDER_EXPIRED", "FILLED"}:
                return
            event_id = "execution-" + __import__("hashlib").sha256(
                f"{assignment['assignment_id']}|{event_type}|"
                f"{getattr(order, 'order_id', None)}|{getattr(fill, 'request_id', None)}".encode()
            ).hexdigest()
            self._journal.record_execution_event(
                assignment["assignment_id"], event_id=event_id, event_type=event_type,
                observed_at=str(getattr(event, "timestamp")),
                order_id=None if order is None else getattr(order, "order_id", None),
                fill_id=None if fill is None else getattr(fill, "request_id", None),
                price=(getattr(fill, "fill_price", None) if fill is not None else
                       getattr(request, "limit_price", None) if request is not None else
                       getattr(order, "limit_price", None)),
                quantity=None if fill is None else getattr(fill, "quantity", None),
                event={"paper_event": raw_type, "lifecycle_id": str(lifecycle)},
            )
            # A sell-side fill is the first authoritative downstream event
            # that carries realized outcome information.  Record it through
            # the existing idempotent outcome journal; entry fills remain
            # execution events, not completed outcomes.
            if event_type == "FILLED" and fill is not None and str(
                getattr(fill, "side", "")
            ).upper() == "SELL":
                outcome_id = "outcome-" + event_id.removeprefix("execution-")
                self._journal.record_outcome(
                    assignment["assignment_id"],
                    {
                        "realized_pnl": getattr(fill, "realized_pnl", None),
                        "fill_price": getattr(fill, "fill_price", None),
                        "quantity": getattr(fill, "quantity", None),
                        "event_type": raw_type,
                    },
                    outcome_id=outcome_id,
                    trade_id=str(lifecycle),
                    order_id=None if order is None else getattr(order, "order_id", None),
                )
        except Exception:
            return

    def _eligible(self, result: HistoricalIntelligenceResult,
                  setup: SetupQualityAssessment, entry: EntryQualityAssessment,
                  candidate: object) -> bool:
        readiness = {
            str(row.get("strategy")) for row in result.readiness_memberships
            if str(row.get("state")) == "TRIGGER_ARMED"
        }
        candidate_setup = getattr(candidate, "setup", None)
        candidate_strategy = None if candidate_setup is None else getattr(
            candidate_setup, "taxonomy_strategy_id", None,
        )
        if candidate_strategy is None and candidate_setup is not None:
            setup_type = getattr(getattr(candidate_setup, "setup_type", None), "value", None)
            candidate_strategy = {
                "HIGH_OF_DAY_BREAKOUT": "HIGH_OF_DAY_BREAKOUT",
                "FLAT_TOP_BREAKOUT": "FLAT_TOP_BREAKOUT",
            }.get(setup_type)
        # Confluence is retained, but an unsupported membership cannot inherit
        # an armed fact from a different membership. Taxonomy candidates carry
        # their selected strategy explicitly; legacy candidates use the DI
        # primary strategy as their provenance.
        membership_matches = (
            False if candidate_strategy is None
            else str(candidate_strategy) in readiness
        )
        return bool(
            result.setup_stage == "TRIGGER_READY"
            and membership_matches
            and setup.state in {"STRONG", "MODERATE"}
            and entry.state in {"FAVORABLE", "ACCEPTABLE"}
            and result.trigger_price is not None
            and result.structural_stop is not None
            and result.structural_stop < result.trigger_price
        )

    @staticmethod
    def _setup_quality(result: HistoricalIntelligenceResult | None) -> SetupQualityAssessment:
        if result is None or not result.setup_evidence:
            return SetupQualityAssessment("INSUFFICIENT_EVIDENCE", blocking_factors=("NO_DI_EVIDENCE",))
        factors: list[str] = []
        blocking: list[str] = []
        confidence = {str(row.get("confidence_state")) for row in result.setup_evidence}
        if confidence & {"STRONG_EVIDENCE", "MODERATE_EVIDENCE"}:
            factors.append("CONFIDENCE")
        else:
            blocking.append("WEAK_OR_MISSING_CONFIDENCE")
        test_samples = [int(row["test_sample_count"]) for row in result.setup_evidence
                        if row.get("test_sample_count") is not None]
        if test_samples and max(test_samples) >= 30:
            factors.append("TEST_SUPPORT")
        else:
            blocking.append("INSUFFICIENT_TEST_SAMPLE")
        if any(row.get("walk_forward_state") for row in result.setup_evidence):
            factors.append("WALK_FORWARD_RECORDED")
        if blocking:
            state = "WEAK" if factors else "INSUFFICIENT_EVIDENCE"
        elif len(factors) >= 3:
            state = "STRONG"
        else:
            state = "MODERATE"
        return SetupQualityAssessment(state, tuple(factors), tuple(blocking))

    @staticmethod
    def _entry_quality(result: HistoricalIntelligenceResult | None) -> EntryQualityAssessment:
        if result is None:
            return EntryQualityAssessment("INSUFFICIENT_DATA", ("NO_DI_RESULT",))
        if result.entry_location == "HEAVILY_EXTENDED":
            return EntryQualityAssessment("CHASE_RISK", ("HEAVILY_EXTENDED",))
        if result.entry_location in {"MODERATELY_EXTENDED", "SLIGHTLY_EXTENDED"}:
            return EntryQualityAssessment("LATE_ENTRY_RISK", (result.entry_location,))
        if result.entry_assessment == "FAVORABLE_LOCATION" or result.entry_location in {
            "APPROACHING_TRIGGER", "AT_TRIGGER",
        }:
            return EntryQualityAssessment("FAVORABLE", ())
        if result.entry_assessment == "ACCEPTABLE_LOCATION" or result.entry_location == "PRE_TRIGGER":
            return EntryQualityAssessment("ACCEPTABLE", ())
        return EntryQualityAssessment("INSUFFICIENT_DATA", (result.entry_location,))


def _triggered_state(state: object) -> object:
    """Keep the enum type while projecting only the treatment candidate."""
    enum_type = type(state)
    return enum_type.TRIGGERED


__all__ = [
    "EntryIntelligenceConfig", "EntryQualityAssessment", "EXPERIMENT_VERSION",
    "HistoricalPaperEntryTimingPolicy", "PaperEntryIntelligenceDecision",
    "SetupQualityAssessment", "entry_experiment_definition",
]
