"""Point-in-time Warrior observation, paper lifecycle, and counterfactual sidecar."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from time import perf_counter
from typing import Callable, Iterable

from app.performance_diagnostics import performance_diagnostics
from app.configuration.models import PaperSymbolAuthorizationMode
from app.momentum_scanner.models import ExecutionQuality

from .configuration import WARRIOR_ENTRY_ALLOWED_SESSIONS, WarriorMomentumConfig
from .features import build_features, canonical_completed_history
from .forward_models import (
    CaptureRecord, CaptureRecordType, FloatProvenance,
    ForwardCaptureConfiguration, ForwardTransition, PaperAccountContext,
    PaperSymbolAuthorization, PaperSymbolAuthorizationSource,
    PointInTimeObservation,
    records_with_configuration_fingerprint,
)
from .autonomous_paper import lifecycle_identity
from .execution_quote import ExecutionQuoteSource
from .entry_economics import remaining_reward_ok
from .entry_extension import assess_entry_extension
from .execution_pursuit import (
    ExecutionPursuitAssessment, ExecutionPursuitDecision,
    assess_top_of_book_pursuit, depth_features, depth_transition,
)
from .adaptive_exit import (
    AdaptiveExitAssessment, AdaptiveExitEvidence, adapt_initial_stop,
    assess_adaptive_exit,
)
from .forward_queue import ForwardCaptureWriter
from .forward_store import ForwardCaptureStore
from .projection_handoff import BoundedProjectionHandoff
from .completed_bar_handoff import (
    BoundedCompletedBarResearchHandoff,
    CompletedBarResearchMetrics,
    CompletedBarResearchWork,
    CompletedBarSubmitResult,
)
from .intelligence_handoff import (
    BoundedIntelligenceHandoff, IntelligenceWorkerMetrics,
)
from .autonomous_paper import (
    PaperEntryAuthorizationDecision, PaperEntryAuthorizationReason,
    PaperEntryAuthorizationResult, PaperEntryGateDecision,
    PaperExitSubmissionDecision, PaperExitSubmissionState, lifecycle_identity, opportunity_identity,
    PaperEntryReplacementDecision, PaperEntryReplacementPolicy,
)
from .models import (
    CandidateStatus, MinuteBar, MomentumCandidate, MomentumEntrySignal,
    ReasonCode, SetupDetection, SetupState, SetupType,
)
from .opportunity_engine import (
    AdaptiveOpportunityResult, OpportunityReason, WarriorOpportunityAssessment,
    WarriorOpportunityEngine, WarriorOpportunityState,
    WarriorOpportunityTransition,
)
from .risk import size_position
from .quick_scalper import StrategyOwner, SymbolOwnershipRegistry
from .session_risk import (
    assess_overnight_carry, entry_cutoff_reached, flatten_window_reached,
    overnight_session_follows,
)
from .runtime import WarriorMomentumRuntime, entry_rejections, execution_liquidity_ok
from .shadow_analysis import ShadowOpportunityAnalyzer
from .shadow_latched import (
    ShadowLatchedPlanResearch,
    ShadowLatchedTransition,
    ShadowMarketObservation,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


# Every currently supported Warrior entry is a long momentum structure whose
# trigger represents executable acceptance above a defined level. Keep this
# list explicit so a future non-breakout setup is not silently subjected to a
# bid-at-trigger rule without a deliberate policy decision.
_EXECUTABLE_TRIGGER_CONFIRMATION_SETUPS = frozenset({
    SetupType.HIGH_OF_DAY_BREAKOUT,
    SetupType.MICRO_PULLBACK,
    SetupType.BULL_FLAG,
    SetupType.FLAT_TOP_BREAKOUT,
    SetupType.MOMENTUM_ACCELERATION,
    SetupType.MOMENTUM_REACCELERATION,
    SetupType.RECLAIM_CONTINUATION,
})


def _requires_executable_trigger_confirmation(setup_type: SetupType) -> bool:
    return setup_type in _EXECUTABLE_TRIGGER_CONFIRMATION_SETUPS


@contextmanager
def _service_stage(name: str, *, symbol: str | None = None):
    """Bounded timing for observational evaluation sub-stages.

    This is deliberately diagnostic-only: failures and return values remain
    owned by the caller, while timing failures can never alter the decision.
    """
    started = perf_counter()
    success = False
    try:
        yield
        success = True
    finally:
        try:
            performance_diagnostics.record_component_duration(
                f"warrior.service.{name}",
                (perf_counter() - started) * 1000.0,
                symbol=symbol,
                success=success,
            )
        except Exception:
            pass


@dataclass(frozen=True, slots=True)
class _IntelligenceIdentity:
    symbol: str
    trading_date: object
    session: str
    completed_bar_version: tuple[object, ...]
    setup_version: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _IntelligenceRequest:
    identity: _IntelligenceIdentity
    value: object
    candidate: MomentumCandidate
    signal: MomentumEntrySignal | None
    legacy_candidate: MomentumCandidate
    stale: bool


@dataclass(frozen=True, slots=True)
class _IntelligenceResult:
    intelligence_result: object | None
    treatment_signal: object | None
    treatment_setup: object | None
    taxonomy_candidate: object | None
    treatment_decision: object | None = None
    treatment_lifecycle_id: str | None = None
    canonical_signal_present: bool = False
    canonical_lifecycle_id: str | None = None


@dataclass(frozen=True, slots=True)
class _PendingTreatmentSignal:
    identity: _IntelligenceIdentity
    symbol: str
    lifecycle_id: str
    value: PointInTimeObservation
    account: PaperAccountContext | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _CompletedBarSnapshot:
    source_bars: tuple[MinuteBar, ...]
    session: str
    cutoff_minute: datetime
    revision: int
    completed: tuple[MinuteBar, ...]


@dataclass(frozen=True, slots=True)
class CompletedBarProcessingMetrics:
    cache_hit: int = 0
    cache_miss: int = 0
    new_bars_processed: int = 0
    duplicate_bars_suppressed: int = 0
    lifecycle_catchup_bars: int = 0
    research_submit: int = 0
    research_duplicate_suppressed: int = 0
    research_worker: CompletedBarResearchMetrics = CompletedBarResearchMetrics()

def management_context_available(
    storage_path, symbol: str, lifecycle_id: str | None = None,
    configuration_fingerprint: str | None = None,
    *, allow_compatible_generation: bool = False,
) -> str | None:
    """Return the matching active PAPER lifecycle ID, when available.

    This hot execution-readiness path is deliberately bounded to the newest
    symbol-local lifecycle records. It must never materialize the full forward
    capture corpus as that can stall market-event processing for seconds.
    """
    try:
        store = ForwardCaptureStore(storage_path)
        contexts = store.latest_records_for_symbol(
            symbol=symbol,
            record_type=CaptureRecordType.MANAGEMENT_CONTEXT,
            limit=512,
        )
        if not contexts:
            return None

        records = contexts
        if configuration_fingerprint is not None:
            direct = tuple(
                record for record in contexts
                if record.payload.get("configuration_fingerprint")
                == configuration_fingerprint
            )
            records = direct
            if not records and allow_compatible_generation:
                entries = {
                    str(item.payload.get("lifecycle_id") or lifecycle_identity(
                        _signal_from_entry(item, item.payload)
                    )): item
                    for item in store.latest_records_for_symbol(
                        symbol=symbol,
                        record_type=CaptureRecordType.PAPER_FILL,
                        limit=512,
                    )
                    if item.payload.get("action") == "ENTRY"
                }
                compatible: list[CaptureRecord] = []
                for item in contexts:
                    payload = item.payload
                    fingerprint = payload.get("configuration_fingerprint")
                    entry = entries.get(str(payload.get("lifecycle_id")))
                    try:
                        trigger = Decimal(entry.payload["entry_trigger"])
                        risk = Decimal(entry.payload["risk_per_share"])
                        targets = tuple(Decimal(value) for value in entry.payload["targets"])
                    except (AttributeError, KeyError, TypeError, ValueError):
                        continue
                    if (
                        fingerprint not in (None, configuration_fingerprint)
                        and entry is not None
                        and payload.get("environment") == "PAPER"
                        and payload.get("strategy") == "WARRIOR_MOMENTUM_V1"
                        and payload.get("phase") in {"MANAGING", "EXIT_WORKING"}
                        and payload.get("structural_stop") == entry.payload.get("structural_stop")
                        and payload.get("planned_entry") == entry.payload.get("fill_price")
                        and targets == (trigger + risk, trigger + risk * 2, trigger + risk * 3)
                    ):
                        compatible.append(item)
                records = tuple(compatible)

        if not records:
            return None

        # latest_records_for_symbol is newest-first. The newest matching
        # lifecycle owns recovery; a CLOSED record must never revive an older
        # MANAGING state.
        for record in records:
            payload = record.payload
            candidate = payload.get("lifecycle_id")
            if lifecycle_id is not None and candidate != lifecycle_id:
                continue
            if payload.get("environment") == "PAPER" and bool(candidate):
                if payload.get("phase") not in {"MANAGING", "EXIT_WORKING"}:
                    return None
                return str(candidate) if payload.get("stop") is not None else None
        return None
    except Exception:
        return None


@dataclass(slots=True)
class _AddOnLeg:
    add_on_id: str
    parent_lifecycle_id: str
    signal: MomentumEntrySignal
    requested_quantity: int
    filled_quantity: int = 0
    remaining: int = 0
    active: bool = True
    peak_price: Decimal | None = None
    peak_r: Decimal | None = None
    current_r: Decimal | None = None
    stop: Decimal | None = None
    risk_consumed: Decimal = ZERO


@dataclass(slots=True)
class _PaperState:
    signal: MomentumEntrySignal
    entry_price: Decimal
    initial_quantity: int
    remaining: int
    stop: Decimal
    first_quantity: int
    second_quantity: int
    managed_quantity: int = 0
    first_taken: bool = False
    second_taken: bool = False
    realized_pnl: Decimal = ZERO
    last_bar_timestamp: datetime | None = None
    prior_low: Decimal | None = None
    minimum_low: Decimal | None = None
    maximum_high: Decimal | None = None
    peak_price: Decimal | None = None
    peak_r: Decimal | None = None
    current_r: Decimal | None = None
    giveback_r: Decimal | None = None
    giveback_fraction_of_peak: Decimal | None = None
    profit_defense_armed: bool = False
    profit_defense_stop_tightened: bool = False
    profit_defense_runner_exit: bool = False
    profit_defense_last_action: str | None = None
    authoritative_position_seen: bool = False
    exit_reason: str | None = None
    exit_price: Decimal | None = None
    # The mutable exit_reason is retained for compatibility with the
    # management state model, but fill attribution must use the durable order
    # role whenever the PAPER bridge can provide it.
    active_exit_role: str | None = None
    active_exit_order_id: str | None = None
    protective_stop_activated_at: datetime | None = None
    protection_reconciled: bool = False
    risk_budget: Decimal = ZERO
    add_on: _AddOnLeg | None = None
    add_on_used: bool = False
    latest_exit_evidence: AdaptiveExitEvidence | None = None
    recent_ranges: tuple[Decimal, ...] = ()
    prior_close: Decimal | None = None
    adaptive_exit_assessment: AdaptiveExitAssessment | None = None
    initial_stop: Decimal | None = None
    peak_executable_bid: Decimal | None = None
    peak_executable_pnl: Decimal = ZERO
    peak_executable_r: Decimal | None = None
    realized_from_partials: Decimal = ZERO
    current_secured_profit: Decimal = ZERO
    peak_to_current_giveback: Decimal = ZERO
    profit_harvest_stage: int = 0
    pending_profit_harvest_role: str | None = None
    entry_fill_recorded: bool = False


@dataclass(frozen=True, slots=True)
class _DurableExitLeg:
    """A bracket leg derived exclusively from the canonical PAPER ledger."""

    role: str
    working_quantity: int = 0
    matching_order: object | None = None
    active_orders: tuple[object, ...] = ()

    @property
    def is_exact(self) -> bool:
        return self.matching_order is not None


@dataclass(slots=True)
class _CounterState:
    symbol: str
    started_at: datetime
    trigger: Decimal
    stop: Decimal
    bars_observed: int = 0
    last_bar_timestamp: datetime | None = None


class WarriorForwardCaptureService:
    """Consumes sanitized observations and never owns a broker/order port."""

    def __init__(
        self, store: ForwardCaptureStore, writer: ForwardCaptureWriter,
        config: WarriorMomentumConfig = WarriorMomentumConfig(),
        capture_config: ForwardCaptureConfiguration = ForwardCaptureConfiguration(),
        paper_entry_submitter: Callable[
            [MomentumEntrySignal, int, Decimal],
            bool | PaperEntryAuthorizationDecision,
        ] | None = None,
        paper_exit_submitter: Callable[[str, int, Decimal, str, str | None], object] | None = None,
        paper_entry_replacer: Callable[..., PaperEntryReplacementDecision] | None = None,
        paper_entry_rearmer: Callable[..., object] | None = None,
        paper_position_quantity_source: Callable[[str], Decimal] | None = None,
        paper_execution_ownership_source: Callable[[str], bool] | None = None,
        paper_exit_fill_source: Callable[[str, str | None], tuple[str | None, str | None]] | None = None,
        paper_working_entry_source: Callable[[str, str], bool] | None = None,
        execution_quote_source: ExecutionQuoteSource | None = None,
        execution_permitted: Callable[[], bool] | None = None,
        account_refresh_source: Callable[[], PaperAccountContext | None] | None = None,
        entry_value_observer: Callable[..., None] | None = None,
        configuration_fingerprint: str | None = None,
        paper_campaign_id: str | None = None,
        paper_add_on_submitter: Callable[..., object] | None = None,
        taxonomy_execution_bridge: object | None = None,
        decision_intelligence_observer: Callable[..., None] | None = None,
        decision_intelligence_entry_observer: Callable[..., tuple[object | None, object | None]] | None = None,
        paper_entry_intelligence: Callable[..., object] | None = None,
        opportunity_engine: WarriorOpportunityEngine | None = None,
        strategy_ownership: SymbolOwnershipRegistry | None = None,
        async_observation_records: bool = False,
        async_decision_intelligence: bool = False,
    ) -> None:
        self.store = store
        self.writer = writer
        self.config = config
        self.capture_config = capture_config
        if configuration_fingerprint is None:
            # Import lazily to preserve the existing fingerprint source of
            # truth without introducing a module import cycle.
            from .desktop_sidecar import strategy_configuration_fingerprint
            configuration_fingerprint = strategy_configuration_fingerprint(config)
        self._paper_entry_submitter = paper_entry_submitter
        self._paper_exit_submitter = paper_exit_submitter
        self._paper_entry_replacer = paper_entry_replacer
        self._paper_entry_rearmer = paper_entry_rearmer
        self._paper_position_quantity_source = paper_position_quantity_source
        self._paper_execution_ownership_source = paper_execution_ownership_source
        self._paper_exit_fill_source = paper_exit_fill_source
        self._paper_working_entry_source = paper_working_entry_source
        self._execution_quote_source = execution_quote_source
        self._execution_permitted = execution_permitted or (lambda: True)
        self._account_refresh_source = account_refresh_source
        self._entry_value_observer = entry_value_observer
        self.configuration_fingerprint = configuration_fingerprint
        self.paper_campaign_id = paper_campaign_id
        self._paper_add_on_submitter = paper_add_on_submitter
        self._taxonomy_execution_bridge = taxonomy_execution_bridge
        self._decision_intelligence_observer = decision_intelligence_observer
        self._decision_intelligence_entry_observer = decision_intelligence_entry_observer
        self._paper_entry_intelligence = paper_entry_intelligence
        self.opportunity_engine = opportunity_engine or WarriorOpportunityEngine(
            observer=self._observe_opportunity_transition,
        )
        # Bounded generation-bound signal provenance used only to reevaluate
        # recoverable ARMED/WAITING opportunities from later streaming events.
        # It cannot create a new generation or survive structural mismatch.
        self._armed_signals: OrderedDict[
            tuple[str, str], MomentumEntrySignal
        ] = OrderedDict()
        self._armed_signal_capacity = 128
        self.strategy_ownership = strategy_ownership
        self._async_decision_intelligence = bool(async_decision_intelligence)
        self._treatment_policy_enabled = self._resolve_treatment_policy_enabled()
        self._intelligence_redrive_enabled = True
        self._pending_treatment: OrderedDict[str, _PendingTreatmentSignal] = OrderedDict()
        self._pending_treatment_capacity = 64
        self._pending_treatment_lifetime_seconds = Decimal("5")
        self._latest_observations: OrderedDict[
            str, tuple[PointInTimeObservation, PaperAccountContext | None]
        ] = OrderedDict()
        self._latest_observation_capacity = 128
        # Worker-published immutable snapshot.  Event reads never acquire the
        # intelligence worker/SQLite lock.
        self._linked_treatment_lifecycles: tuple[str, ...] = ()
        self._intelligence_handoff = (
            BoundedIntelligenceHandoff(
                self._evaluate_intelligence_serialized,
                maximum_keys=128,
                autostart=True,
                completion_callback=self._on_intelligence_publication,
            )
            if async_decision_intelligence and (
                taxonomy_execution_bridge is not None
                or decision_intelligence_observer is not None
                or decision_intelligence_entry_observer is not None
            )
            else None
        )
        self._observation_record_handoff = (
            BoundedProjectionHandoff(
                self._dispatch_observation_records,
                maximum_keys=512,
                autostart=True,
            )
            if async_observation_records else None
        )
        self._pretrigger_shadow = None
        if (
            capture_config.shadow_analysis_enabled
            and (
                decision_intelligence_observer is not None
                or decision_intelligence_entry_observer is not None
            )
        ):
            from app.trade_intelligence.pretrigger_shadow import (
                AdaptivePretriggerShadowIntelligence,
            )
            self._pretrigger_shadow = AdaptivePretriggerShadowIntelligence(
                writer,
                stale_after_seconds=capture_config.quote_stale_after_seconds,
                configuration_fingerprint=configuration_fingerprint,
                store=store,
            )
        # Observation-only continuity state.  It never participates in entry
        # authorization or order submission.
        from app.trade_intelligence.opportunity_memory import OpportunityMemory
        self.opportunity_memory = OpportunityMemory()
        # These are latest-only continuity hints.  Durable opportunity
        # history lives in OpportunityMemory/forward capture; the hints are
        # bounded to that existing opportunity-memory capacity.
        self._memory_opportunity_ids: OrderedDict[str, str] = OrderedDict()
        self._memory_geometry_keys: OrderedDict[str, str] = OrderedDict()
        self.runtime = WarriorMomentumRuntime(config)
        self._completed_bar_cache: OrderedDict[str, _CompletedBarSnapshot] = OrderedDict()
        self._completed_bar_cache_hits = 0
        self._completed_bar_cache_misses = 0
        self._completed_bar_new_bars_processed = 0
        self._completed_bar_duplicate_bars_suppressed = 0
        self._completed_bar_lifecycle_catchup_bars = 0
        self._completed_bar_research_submits = 0
        self._completed_bar_research_duplicate_suppressed = 0
        self._completed_bar_capture_cursor: dict[str, datetime] = {}
        self._completed_bar_capture_revision: dict[str, tuple[str, int]] = {}
        self._completed_bar_research_cursor: dict[str, datetime] = {}
        self._last_transition: dict[str, ForwardTransition] = {}
        # Bounded, transition-only setup timing evidence. This is a
        # diagnostic/read-model aid and never participates in authorization.
        self._setup_lifecycles: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._setup_lifecycle_capacity = 512
        # Compact, latest-only execution-pursuit diagnostics.  This is not a
        # per-tick history and never becomes an execution authority.
        self._last_execution_pursuit: dict[str, ExecutionPursuitAssessment] = {}
        self._last_depth_by_symbol: dict[str, tuple[tuple, tuple]] = {}
        self._seen_bars: set[tuple[str, datetime]] = set()
        self._paper: dict[str, _PaperState] = {}
        self._counterfactual: dict[str, _CounterState] = {}
        # Prospective-only execution paths.  The caps prevent a bad identity
        # stream from turning research capture into unbounded process state.
        self._execution_paths: OrderedDict[tuple[str, str], dict[str, object]] = OrderedDict()
        self._execution_path_max_active = 128
        self._execution_path_max_samples = 20_000
        self._execution_path_min_interval_seconds = 1.0
        self._execution_path_gap_seconds = 5.0
        self._shadow = (
            ShadowOpportunityAnalyzer(
                store, configuration_fingerprint=configuration_fingerprint,
            )
            if capture_config.shadow_analysis_enabled else None
        )
        self._latched_shadow = (
            ShadowLatchedPlanResearch(config, capture_config)
            if capture_config.shadow_analysis_enabled else None
        )
        self._recover()
        self._completed_bar_research_handoff = (
            BoundedCompletedBarResearchHandoff(
                self._process_completed_bar_research,
                capacity=4096,
                autostart=False,
            )
            if self._shadow is not None else None
        )
        for seen_symbol, seen_timestamp in self._seen_bars:
            current = self._completed_bar_capture_cursor.get(seen_symbol)
            if current is None or seen_timestamp > current:
                self._completed_bar_capture_cursor[seen_symbol] = seen_timestamp

    @staticmethod
    def _opportunity_generation(signal: MomentumEntrySignal) -> str:
        return lifecycle_identity(signal)

    def _remember_armed_signal(self, signal: MomentumEntrySignal) -> None:
        key = (signal.symbol.strip().upper(), self._opportunity_generation(signal))
        self._armed_signals[key] = signal
        self._armed_signals.move_to_end(key)
        while len(self._armed_signals) > self._armed_signal_capacity:
            self._armed_signals.popitem(last=False)

    def _retained_generation_signal(
        self, candidate: MomentumCandidate,
    ) -> MomentumEntrySignal | None:
        """Rebuild the current event for the same recoverable generation.

        A prior trigger may be represented as FORMING, or temporarily have no
        selected setup, on a later completed-bar detector tick. The already
        armed generation remains authoritative only for the detector's bounded
        continuity interval, in the same session, above its structural stop,
        and absent explicit structural invalidation. A different setup,
        expired generation, invalidation, or stop breach cannot resurrect it.
        """
        current = self.opportunity_engine.get(candidate.symbol)
        if current is None or current.state not in {
            WarriorOpportunityState.ARMED,
            WarriorOpportunityState.WAITING_EXECUTION,
        }:
            return None
        generation = current.assessment.generation_id
        retained = self._armed_signals.get(
            (candidate.symbol.strip().upper(), generation)
        )
        setup = candidate.setup
        if retained is None:
            return None
        if candidate.price <= retained.stop_price:
            self.opportunity_engine.invalidate(
                candidate.symbol, generation, at=candidate.timestamp,
                reason=OpportunityReason.SETUP_INVALIDATED,
            )
            return None
        age = candidate.timestamp - retained.timestamp
        if (
            candidate.session != retained.session
            or age < timedelta(0)
            or age > self.runtime.setup_continuity_age
        ):
            self.opportunity_engine.expire(
                candidate.symbol, generation, at=candidate.timestamp,
            )
            return None
        if setup is None:
            evidence = candidate.setup_evidence
            if evidence is not None and evidence.structural_invalidation:
                self.opportunity_engine.invalidate(
                    candidate.symbol, generation, at=candidate.timestamp,
                    reason=OpportunityReason.SETUP_INVALIDATED,
                )
                return None
            setup = SetupDetection(
                setup_type=retained.setup_type,
                state=SetupState.TRIGGERED,
                score=retained.setup_score,
                trigger=retained.entry_trigger,
                stop_price=retained.stop_price,
                stop_model=retained.stop_model,
                structural_episode_id=retained.structural_episode_id,
                structural_anchor=retained.structural_anchor,
                structural_entry_trigger=(
                    retained.structural_entry_trigger or retained.entry_trigger
                ),
            )
        state = getattr(setup.state, "value", setup.state)
        if state not in {SetupState.FORMING.value, SetupState.TRIGGERED.value}:
            return None
        retained_episode = str(retained.structural_episode_id or "").strip()
        current_episode = str(setup.structural_episode_id or "").strip()
        same_structure = bool(
            retained_episode and current_episode
            and retained_episode == current_episode
        )
        if not same_structure:
            same_structure = (
                setup.setup_type is retained.setup_type
                and setup.trigger == retained.entry_trigger
                and setup.stop_price == retained.stop_price
            )
        if not same_structure:
            return None
        rebound = replace(
            candidate,
            setup=replace(
                setup, state=SetupState.TRIGGERED,
                trigger=retained.entry_trigger,
                stop_price=retained.stop_price,
                stop_model=retained.stop_model,
                structural_episode_id=retained.structural_episode_id,
                structural_entry_trigger=(
                    retained.structural_entry_trigger or retained.entry_trigger
                ),
            ),
        )
        refreshed = self.runtime.technical_entry_signal(rebound)
        if (
            refreshed is None
            or self._opportunity_generation(refreshed) != generation
        ):
            return None
        return refreshed

    def _observe_opportunity_transition(
        self, transition: WarriorOpportunityTransition,
    ) -> None:
        stage = {
            WarriorOpportunityState.ARMED: "OPPORTUNITY_ARMED",
            WarriorOpportunityState.WAITING_EXECUTION: "OPPORTUNITY_WAITING_EXECUTION",
            WarriorOpportunityState.EXECUTABLE: "OPPORTUNITY_EXECUTABLE",
            WarriorOpportunityState.AUTHORIZATION_EVALUATED: "OPPORTUNITY_AUTHORIZATION_EVALUATED",
            WarriorOpportunityState.AUTHORIZED: "OPPORTUNITY_AUTHORIZED",
            WarriorOpportunityState.INVALIDATED: "OPPORTUNITY_INVALIDATED",
            WarriorOpportunityState.EXPIRED: "OPPORTUNITY_EXPIRED",
            WarriorOpportunityState.REJECTED_HARD_SAFETY: "OPPORTUNITY_REJECTED_HARD_SAFETY",
        }.get(transition.state)
        if stage is None:
            return
        try:
            performance_diagnostics.record_entry_funnel(
                transition.symbol, stage=stage,
                outcome=transition.state.value,
                reason=transition.reason.value,
                timestamp=transition.occurred_at,
                lifecycle_id=transition.lifecycle_id,
                generation_id=transition.generation_id,
            )
        except Exception:
            return

    @staticmethod
    def _bind_opportunity_state(
        candidate: MomentumCandidate, opportunity: object | None,
    ) -> MomentumCandidate:
        if opportunity is None:
            return candidate
        assessment = getattr(opportunity, "assessment", None)
        state = getattr(opportunity, "state", None)
        reason = getattr(opportunity, "reason", None)
        return replace(
            candidate,
            opportunity_state=getattr(state, "value", None),
            opportunity_generation_id=getattr(assessment, "generation_id", None),
            opportunity_reason=getattr(reason, "value", None),
        )

    def _build_opportunity_assessment(
        self, value: PointInTimeObservation, candidate: MomentumCandidate,
        signal: MomentumEntrySignal, *,
        adaptive_result: AdaptiveOpportunityResult,
        executable_signal: MomentumEntrySignal | None = None,
        hard_safety_reason: OpportunityReason | None = None,
        reason_codes: tuple[OpportunityReason, ...] = (),
    ) -> WarriorOpportunityAssessment:
        observation = value.observation
        decision_at = value.evaluation_timestamp or observation.timestamp
        structural = signal.structural_entry_trigger or signal.entry_trigger
        executable_entry = (
            executable_signal.entry_trigger if executable_signal is not None
            else observation.ask
        )
        spread_cost = (
            None if observation.bid is None or observation.ask is None
            else observation.ask - observation.bid
        )
        first_r = final_r = None
        if (
            executable_entry is not None and spread_cost is not None
            and spread_cost >= ZERO and executable_entry > signal.stop_price
        ):
            risk = executable_entry - signal.stop_price + spread_cost
            if risk > ZERO and len(signal.target_levels) >= 2:
                first_r = (signal.target_levels[0] - executable_entry - spread_cost) / risk
                final_r = (signal.target_levels[-1] - executable_entry - spread_cost) / risk
        displacement = (
            None if executable_entry is None or structural <= ZERO
            else (executable_entry - structural) / structural * HUNDRED
        )
        generation = self._opportunity_generation(signal)
        return WarriorOpportunityAssessment(
            symbol=signal.symbol, setup_family=signal.setup_type.value,
            generation_id=generation, lifecycle_id=generation,
            decision_timestamp=decision_at,
            scanner_timestamp=candidate.scanner_observation_timestamp or candidate.timestamp,
            quote_timestamp=value.quote_observed_at,
            last_timestamp=value.last_price_observed_at,
            structural_trigger=structural, executable_entry=executable_entry,
            structural_stop=signal.stop_price,
            risk_per_share=(
                executable_signal.risk_per_share if executable_signal is not None
                else signal.risk_per_share
            ),
            target_levels=signal.target_levels,
            remaining_first_target_r=first_r, remaining_final_target_r=final_r,
            spread_percent=candidate.spread_percent, execution_cost=spread_cost,
            execution_quality=candidate.execution_quality.value,
            dollar_liquidity=candidate.dollar_volume,
            relative_volume=candidate.relative_volume,
            volatility_context=candidate.risk_velocity_r_per_minute,
            catalyst_context=candidate.catalyst_status.value,
            setup_quality=signal.setup_score,
            momentum_priority=candidate.momentum_priority,
            displacement_percent=displacement,
            adaptive_result=adaptive_result,
            momentum_score=candidate.score.total,
            hard_safety_reason=hard_safety_reason, reason_codes=reason_codes,
            strategy="WARRIOR_MOMENTUM",
            executable_bid=observation.bid,
            provider_last_timestamp=value.last_price_observed_at,
            provider_bid_timestamp=value.quote_observed_at,
            provider_ask_timestamp=value.quote_observed_at,
            execution_quote_timestamp=decision_at,
        )

    def _assess_executable_opportunity(
        self, value: PointInTimeObservation, candidate: MomentumCandidate,
        signal: MomentumEntrySignal, *, diagnostic=None,
    ) -> tuple[WarriorOpportunityAssessment, MomentumEntrySignal | None]:
        executable = (
            self._execution_entry_signal(value, candidate, signal, diagnostic=diagnostic)
            if self.config.adaptive_context_enabled else signal
        )
        bid, ask = value.observation.bid, value.observation.ask
        reward_ok = bool(
            executable is not None and bid is not None and ask is not None
            and ask >= bid and remaining_reward_ok(
                entry=executable.entry_trigger, stop=executable.stop_price,
                targets=executable.target_levels, spread=ask - bid,
                minimum_first_r=self.config.entry.minimum_remaining_first_target_r,
                minimum_final_r=self.config.entry.minimum_remaining_final_target_r,
            )
        )
        reasons: list[OpportunityReason] = []
        quality_wait = candidate.execution_quality in {
            ExecutionQuality.POOR, ExecutionQuality.TEMPORARILY_BLOCKED,
        }
        # Evaluate all adaptive constraints from the same point-in-time
        # snapshot.  These are not mutually exclusive gates; retaining the
        # whole constraint set prevents the UI/runtime from cycling through
        # one veto at a time while the same opportunity remains valid.
        if executable is None:
            reasons.append(OpportunityReason.EXCESSIVE_CURRENT_STRUCTURE_DISPLACEMENT)
        if quality_wait:
            reasons.append(OpportunityReason.EXECUTION_QUALITY_WAIT)
        if (
            executable is not None
            and _requires_executable_trigger_confirmation(signal.setup_type)
            and (
                bid is None
                or bid < (
                    signal.structural_entry_trigger or signal.entry_trigger
                )
            )
        ):
            reasons.append(
                OpportunityReason.EXECUTABLE_TRIGGER_NOT_CONFIRMED
            )
        if executable is not None and not reward_ok:
            reasons.append(OpportunityReason.INSUFFICIENT_CURRENT_REWARD)
        # Displacement and remaining reward are current executable-market
        # measurements.  They forbid entry now, but a pullback/tighter market
        # can repair them while the same technical generation remains valid.
        # Structural invalidation is owned by the setup lifecycle, not these
        # adaptive economic checks.
        result = (
            AdaptiveOpportunityResult.WAIT
            if reasons else AdaptiveOpportunityResult.EXECUTABLE
        )
        assessment = self._build_opportunity_assessment(
            value, candidate, signal, adaptive_result=result,
            executable_signal=executable, reason_codes=tuple(reasons),
        )
        return assessment, executable if result is AdaptiveOpportunityResult.EXECUTABLE else None

    def observe(
        self, value: PointInTimeObservation,
        *, account: PaperAccountContext | None = None,
        _treatment_redrive: bool = False,
    ) -> tuple[MomentumCandidate, MomentumEntrySignal | None]:
        observation = value.observation
        symbol = observation.symbol.strip().upper()
        downstream_clear_reason: str | None = None
        self._remember_latest_observation(symbol, value, account)
        with _service_stage("execution_price_path", symbol=symbol):
            self._observe_execution_price_path(value)
        live_state = self._paper.get(symbol)
        if (
            live_state is not None
            and observation.timestamp >= live_state.signal.timestamp
        ):
            self._capture_exit_evidence(live_state, value)
            changed = self._synchronize_authoritative_position(
                live_state, observation.timestamp,
            )
            changed = self._update_executable_profit_state(live_state) or changed
            changed = self._manage_executable_profit_harvest(
                live_state, observation.timestamp,
            ) or changed
            if changed:
                self._submit_records((_management_context_record(
                    symbol, observation.timestamp, live_state.signal, live_state,
                    phase=(
                        "EXIT_WORKING"
                        if live_state.exit_reason is not None else "MANAGING"
                    ),
                ),))
        with _service_stage("completed_bar_handling", symbol=symbol):
            completed_snapshot = self._completed_bar_snapshot(value)
            completed = completed_snapshot.completed
            completed_version = (
                completed_snapshot.session,
                completed_snapshot.cutoff_minute,
                completed_snapshot.revision,
            )
            self._deliver_completed_bar_deltas(
                symbol, completed_snapshot, observation.timestamp,
            )
        with _service_stage("runtime_discover", symbol=symbol):
            candidate = self.runtime.discover(
                observation, completed, session=value.session,
            )
        candidate = _bind_decision_generation(candidate, value)
        if candidate.discovery_qualified:
            performance_diagnostics.record_entry_funnel(
                symbol, stage="SCANNER_QUALIFIED", timestamp=value.evaluation_timestamp or observation.timestamp,
            )
        if candidate.setup is not None:
            if candidate.setup.state.value == "FORMING":
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="SETUP_FORMING", timestamp=value.evaluation_timestamp or observation.timestamp,
                )
            elif candidate.setup.state.value == "TRIGGERED":
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="SETUP_TRIGGERED", timestamp=value.evaluation_timestamp or observation.timestamp,
                )
        legacy_candidate = candidate
        with _service_stage("entry_assessment", symbol=symbol):
            assessed, signal = self.runtime.assess_entry(candidate)
            technical_signal = self.runtime.technical_entry_signal(candidate)
        retained_generation = False
        if technical_signal is None:
            technical_signal = self._retained_generation_signal(candidate)
            retained_generation = technical_signal is not None
        if technical_signal is not None:
            performance_diagnostics.record_entry_funnel(
                symbol, stage="TECHNICAL_SIGNAL", timestamp=value.evaluation_timestamp or observation.timestamp,
            )
            generation = self._opportunity_generation(technical_signal)
            self._remember_armed_signal(technical_signal)
            armed = self.opportunity_engine.get(symbol, generation)
            if armed is None:
                armed = self.opportunity_engine.arm(
                    self._build_opportunity_assessment(
                        value, candidate, technical_signal,
                        adaptive_result=AdaptiveOpportunityResult.ARMED,
                        reason_codes=(OpportunityReason.TECHNICAL_TRIGGER,),
                    )
                )
            assessed = self._bind_opportunity_state(assessed, armed)
            if (
                signal is None
                and (
                    retained_generation
                    or self.runtime.momentum_preference_is_only_entry_rejection(
                        candidate
                    )
                )
            ):
                # Continue through the existing executable-economics and hard
                # safety path. The score remains in the signal/assessment and
                # Opportunity Book rank; it no longer destroys structure.
                signal = technical_signal
                assessed = replace(
                    assessed, status=CandidateStatus.ENTRY_READY,
                    reason_codes=tuple(
                        code for code in assessed.reason_codes
                        if code not in {ReasonCode.RISK_REJECTED, ReasonCode.NO_SETUP}
                    ),
                )
        setup = candidate.setup
        setup_state = "ENTRY_READY" if signal is not None else (
            "NO_SETUP" if setup is None else str(setup.state.value).upper()
        )
        lifecycle_timestamp = value.evaluation_timestamp or observation.timestamp
        self._record_setup_lifecycle(
            value, candidate, setup_state=setup_state,
            technical_signal=technical_signal, signal=None,
            timestamp=lifecycle_timestamp,
        )
        performance_diagnostics.record_setup_transition(
            symbol=symbol,
            state=setup_state,
            lifecycle_id=(None if signal is None else lifecycle_identity(signal)),
            setup_type=(None if setup is None else setup.setup_type.value),
            timestamp=value.evaluation_timestamp or observation.timestamp,
            trigger=(None if setup is None else setup.trigger),
            stop=(None if setup is None else setup.stop_price),
            reason=(None if not assessed.reason_codes else assessed.reason_codes[-1].value),
        )
        market_data_stale = (
            value.last_price_freshness_seconds is None
            or value.quote_freshness_seconds is None
            or value.last_price_freshness_seconds
            > self.capture_config.quote_stale_after_seconds
            or value.quote_freshness_seconds
            > self.capture_config.quote_stale_after_seconds
        )
        processing_delayed = (
            (
                value.processing_age_seconds is not None
                and value.processing_age_seconds
                > self.capture_config.quote_stale_after_seconds
            )
            or (
                not value.retained_reevaluation
                and
                value.delivery_age_seconds is not None
                and value.delivery_age_seconds
                > self.capture_config.quote_stale_after_seconds
            )
        )
        if processing_delayed:
            performance_diagnostics.increment("processing_delayed_events")
        if market_data_stale or processing_delayed:
            downstream_clear_reason = "CLEAR_QUOTE_FRESHNESS"
            performance_diagnostics.record_entry_funnel(
                symbol, stage="FRESHNESS_CHECK", outcome="REJECTED",
                timestamp=value.evaluation_timestamp or observation.timestamp,
                reason="STALE_MARKET_DATA" if market_data_stale else "FRESHNESS_OK_PROCESSING_DELAYED",
            )
        else:
            performance_diagnostics.record_entry_funnel(
                symbol, stage="FRESHNESS_CHECK", timestamp=value.evaluation_timestamp or observation.timestamp,
            )
        performance_diagnostics.record_entry_funnel(
            symbol, stage="PROCESSING_AGE_CHECK",
            outcome="REJECTED" if processing_delayed else "ACCEPTED",
            timestamp=value.evaluation_timestamp or observation.timestamp,
            reason="PROCESSING_DELAYED" if processing_delayed else None,
        )
        performance_diagnostics.record_entry_funnel(
            symbol, stage="RETAINED_SOURCE_AGE",
            outcome=("RETAINED" if value.retained_reevaluation else "CURRENT"),
            timestamp=value.evaluation_timestamp or observation.timestamp,
            retained_source_age=value.retained_source_age_seconds,
            reevaluation_mailbox_age=value.reevaluation_mailbox_age_seconds,
            processing_age=value.processing_age_seconds,
            delivery_age=value.delivery_age_seconds,
        )
        if market_data_stale or processing_delayed:
            if technical_signal is not None:
                wait_reason = (
                    OpportunityReason.PROCESSING_DELAYED
                    if processing_delayed else OpportunityReason.PROVIDER_DATA_STALE
                )
                waiting = self.opportunity_engine.apply_assessment(
                    self._build_opportunity_assessment(
                        value, candidate, technical_signal,
                        adaptive_result=AdaptiveOpportunityResult.WAIT,
                        hard_safety_reason=wait_reason,
                        reason_codes=(wait_reason,),
                    )
                )
                assessed = self._bind_opportunity_state(assessed, waiting)
            assessed = replace(
                assessed,
                status=(CandidateStatus.AWAITING_EXECUTION_DATA
                        if technical_signal is not None
                        else CandidateStatus.INELIGIBLE_FOR_EXECUTION),
                reason_codes=tuple(dict.fromkeys((
                    *assessed.reason_codes,
                    ReasonCode.STALE_MARKET_DATA,
                    *((ReasonCode.PROCESSING_DELAYED,) if processing_delayed else ()),
                    *((ReasonCode.AWAITING_EXECUTION_QUOTE,)
                      if technical_signal is not None and not processing_delayed else ()),
                ))),
            )
            signal = None
            if (
                technical_signal is not None
                and not processing_delayed
                and self._execution_quote_source is not None
            ):
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="EXECUTION_QUOTE_REQUESTED",
                    timestamp=value.evaluation_timestamp or observation.timestamp,
                )
                performance_diagnostics.mark_latency_trace_stage(
                    "execution_quote_requested", True
                )
                try:
                    refreshed = self._execution_quote_source(symbol)
                except Exception:
                    refreshed = None
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="EXECUTION_QUOTE_RETURNED",
                    outcome="AVAILABLE" if refreshed is not None else "UNAVAILABLE",
                    timestamp=value.evaluation_timestamp or observation.timestamp,
                )
                evaluated_at = (
                    (None if refreshed is None else refreshed.confirmed_at)
                    or value.evaluation_timestamp
                    or observation.timestamp
                )
                technical_minute = (
                    value.evaluation_timestamp or observation.timestamp
                ).replace(second=0, microsecond=0)
                provenance_outcome = "EXECUTION_QUOTE_REJECTED"
                provenance_rejection_reason: str | None = "QUOTE_REJECTED_MISSING"
                if refreshed is not None:
                    quote_age = Decimal(str((evaluated_at - refreshed.bid_timestamp).total_seconds()))
                    last_age = Decimal(str((evaluated_at - refreshed.last_timestamp).total_seconds()))
                    if (
                        refreshed.symbol == symbol
                        and Decimal("0") <= quote_age <= self.capture_config.quote_stale_after_seconds
                        and Decimal("0") <= last_age <= self.capture_config.quote_stale_after_seconds
                        # Do not reuse setup geometry if a new minute could
                        # have completed while the bounded request was open.
                        and evaluated_at.replace(second=0, microsecond=0) == technical_minute
                    ):
                        provenance_outcome = "EXECUTION_QUOTE_ACCEPTED"
                        provenance_rejection_reason = None
                        performance_diagnostics.record_entry_funnel(
                            symbol, stage="EXECUTION_QUOTE_ACCEPTED",
                            timestamp=evaluated_at,
                        )
                        refreshed_observation = replace(
                            observation, price=refreshed.last,
                            bid=refreshed.bid, ask=refreshed.ask,
                            quote_timestamp=refreshed.bid_timestamp,
                            last_price_timestamp=refreshed.last_timestamp,
                            quote_received_timestamp=refreshed.confirmed_at,
                            last_price_received_timestamp=refreshed.confirmed_at,
                            bid_size=None, ask_size=None,
                        )
                        refreshed_value = replace(
                            value, observation=refreshed_observation,
                            quote_observed_at=refreshed.bid_timestamp,
                            quote_freshness_seconds=quote_age,
                            last_price_observed_at=refreshed.last_timestamp,
                            last_price_freshness_seconds=last_age,
                            evaluation_timestamp=evaluated_at,
                            best_bid_size=None,
                            best_ask_size=None,
                            quote_provenance="SHARED_EXECUTION_QUOTE_CONFIRMATION",
                            retained_reevaluation=False,
                            retained_source_age_seconds=None,
                            reevaluation_mailbox_age_seconds=Decimal("0"),
                            processing_age_seconds=Decimal("0"),
                            delivery_age_seconds=Decimal("0"),
                        )
                        refreshed_candidate = self.runtime.discover(
                            refreshed_observation, completed, session=value.session,
                        )
                        refreshed_candidate = _bind_decision_generation(
                            refreshed_candidate, refreshed_value,
                        )
                        refreshed_assessed, refreshed_signal = self.runtime.assess_entry(
                            refreshed_candidate
                        )
                        refreshed_technical = self.runtime.technical_entry_signal(
                            refreshed_candidate
                        )
                        expected_generation = self._opportunity_generation(technical_signal)
                        refreshed_generation = (
                            None if refreshed_technical is None
                            else self._opportunity_generation(refreshed_technical)
                        )
                        generation_matches = (
                            refreshed_generation == expected_generation
                            and self.opportunity_engine.quote_matches(
                                symbol, expected_generation,
                            )
                        )
                        # The refreshed assessment is the current decision
                        # generation even when a downstream gate still says
                        # WAIT. Never display the old retained decision beside
                        # the newly confirmed quote.
                        candidate = refreshed_candidate
                        assessed = refreshed_assessed
                        signal = refreshed_signal if generation_matches else None
                        observation = refreshed_observation
                        value = refreshed_value
                        if not generation_matches:
                            downstream_clear_reason = "CLEAR_QUOTE_GENERATION"
                            provenance_outcome = "EXECUTION_QUOTE_REJECTED"
                            provenance_rejection_reason = "QUOTE_REJECTED_OBSOLETE_GENERATION"
                            waiting = self.opportunity_engine.apply_assessment(
                                self._build_opportunity_assessment(
                                    value, refreshed_candidate, technical_signal,
                                    adaptive_result=AdaptiveOpportunityResult.WAIT,
                                    hard_safety_reason=OpportunityReason.OBSOLETE_QUOTE_GENERATION,
                                    reason_codes=(OpportunityReason.OBSOLETE_QUOTE_GENERATION,),
                                )
                            )
                            assessed = self._bind_opportunity_state(assessed, waiting)
                            performance_diagnostics.record_entry_funnel(
                                symbol, stage="EXECUTION_QUOTE_REJECTED",
                                outcome="QUOTE_REJECTED_OBSOLETE_GENERATION",
                                reason="QUOTE_REJECTED_OBSOLETE_GENERATION",
                                timestamp=evaluated_at,
                                lifecycle_id=expected_generation,
                                returned_generation_id=refreshed_generation,
                            )
                        else:
                            technical_signal = refreshed_technical
                        if refreshed_signal is not None:
                            if self._account_refresh_source is not None:
                                account = self._account_refresh_source()
                            if not self._execution_permitted():
                                signal = None
                    else:
                        downstream_clear_reason = "CLEAR_QUOTE_FRESHNESS"
                        quote_timestamp = None if refreshed is None else refreshed.bid_timestamp
                        last_timestamp = None if refreshed is None else refreshed.last_timestamp
                        quote_age_value = None if refreshed is None else quote_age
                        last_age_value = None if refreshed is None else last_age
                        quote_minute = None if refreshed is None else evaluated_at.replace(second=0, microsecond=0)
                        rejection_reason = "QUOTE_REJECTED_MISSING"
                        if refreshed is not None and refreshed.symbol != symbol:
                            rejection_reason = "QUOTE_REJECTED_SYMBOL_MISMATCH"
                        elif refreshed is not None and not (Decimal("0") <= quote_age <= self.capture_config.quote_stale_after_seconds):
                            rejection_reason = "QUOTE_REJECTED_BID_STALE"
                        elif refreshed is not None and not (Decimal("0") <= last_age <= self.capture_config.quote_stale_after_seconds):
                            rejection_reason = "QUOTE_REJECTED_LAST_TRADE_STALE"
                        elif refreshed is not None and evaluated_at.replace(second=0, microsecond=0) != technical_minute:
                            rejection_reason = "QUOTE_REJECTED_TECHNICAL_MINUTE"
                        elif refreshed is not None and (
                            refreshed.bid is None or refreshed.ask is None
                            or refreshed.bid <= 0 or refreshed.ask < refreshed.bid
                        ):
                            rejection_reason = "QUOTE_REJECTED_INVALID"
                        provenance_rejection_reason = rejection_reason
                        performance_diagnostics.record_entry_funnel(
                            symbol, stage="EXECUTION_QUOTE_REJECTED",
                            outcome=rejection_reason,
                            timestamp=evaluated_at,
                            signal_timestamp=value.evaluation_timestamp or observation.timestamp,
                            quote_timestamp=quote_timestamp,
                            bid_timestamp_age=quote_age_value,
                            last_trade_timestamp_age=last_age_value,
                            processing_age=value.processing_age_seconds,
                            delivery_age=value.delivery_age_seconds,
                            technical_signal_minute=technical_minute,
                            quote_minute=quote_minute,
                            bid=(None if refreshed is None else refreshed.bid),
                            ask=(None if refreshed is None else refreshed.ask),
                            spread=(None if refreshed is None or refreshed.bid is None or refreshed.ask is None
                                    else refreshed.ask - refreshed.bid),
                        )
                try:
                    provenance_recorder = getattr(
                        self._execution_quote_source, "record_decision", None,
                    )
                    if callable(provenance_recorder):
                        provenance_recorder(
                            evaluated_at=evaluated_at,
                            snapshot=refreshed,
                            outcome=provenance_outcome,
                            rejection_reason=provenance_rejection_reason,
                        )
                except Exception:
                    pass
        taxonomy_bridge = self._taxonomy_execution_bridge
        intelligence_identity = self._intelligence_identity(
            value, assessed, completed_version,
        )
        cached_intelligence = None
        if self._intelligence_handoff is not None:
            with _service_stage("taxonomy_fast_path", symbol=symbol):
                publication = self._intelligence_handoff.lookup(symbol)
                if (
                    publication is not None
                    and publication.identity == intelligence_identity
                    and isinstance(publication.value, _IntelligenceResult)
                ):
                    cached_intelligence = publication.value
            taxonomy_candidate, taxonomy_signal = None, None
        else:
            with _service_stage("taxonomy_assessment", symbol=symbol):
                taxonomy_candidate, taxonomy_signal = (
                    (None, None)
                    if taxonomy_bridge is None
                    else taxonomy_bridge.evaluate(
                        value, candidate, signal,
                        market_data_stale or processing_delayed,
                        self.runtime, self.capture_config.quote_stale_after_seconds,
                    )
                )
        # Taxonomy output is advisory.  Only the canonical forward Warrior
        # result can create execution authority; a raw research trigger must
        # never overwrite a canonical NO_SETUP result.
        if signal is not None:
            if self._account_refresh_source is not None:
                account = self._account_refresh_source()
            if not self._execution_permitted():
                downstream_clear_reason = "CLEAR_ACCOUNT"
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="EXECUTION_PERMISSION", outcome="REJECTED",
                    reason="EXECUTION_NOT_ALLOWED",
                    timestamp=value.evaluation_timestamp or observation.timestamp,
                )
                assessed = replace(
                    assessed, status=CandidateStatus.INELIGIBLE_FOR_EXECUTION,
                    reason_codes=tuple(dict.fromkeys((
                        *assessed.reason_codes, ReasonCode.EXECUTION_NOT_ALLOWED,
                    ))),
                )
                signal = None
            else:
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="EXECUTION_PERMISSION", outcome="ACCEPTED",
                    timestamp=value.evaluation_timestamp or observation.timestamp,
                )
        # The symbol cache is only a continuity hint.  Once a terminal,
        # unfilled lifecycle has been removed, a changed structural geometry
        # is a new opportunity and must not inherit the old executable
        # authority or DI identity.  Active positions and working entries are
        # deliberately stronger authorities and keep the existing identity.
        if signal is not None:
            self._record_setup_lifecycle(
                value, assessed, setup_state="EXECUTION_ELIGIBLE",
                technical_signal=technical_signal, signal=signal,
                timestamp=value.evaluation_timestamp or observation.timestamp,
            )
            self._reconcile_structural_opportunity(symbol, signal)
        conventional_signal = signal
        intelligence_result = None
        treatment_signal = None
        intelligence_candidate = (
            taxonomy_candidate
            if taxonomy_candidate is not None and signal is None
            else assessed
        )
        intelligence_request = _IntelligenceRequest(
            identity=intelligence_identity,
            value=value,
            candidate=assessed,
            signal=signal,
            legacy_candidate=legacy_candidate,
            stale=market_data_stale or processing_delayed,
        )
        if self._intelligence_handoff is not None:
            treatment_policy_pending = False
            with _service_stage("treatment_lookup", symbol=symbol):
                if (
                    cached_intelligence is not None
                    and (
                        signal is None
                        or cached_intelligence.canonical_signal_present
                        or (
                            cached_intelligence.treatment_lifecycle_id
                            == lifecycle_identity(signal)
                        )
                        or (
                            lifecycle_identity(signal)
                            in self._linked_treatment_lifecycles
                        )
                    )
                ):
                    intelligence_result = cached_intelligence.intelligence_result
                    if signal is None:
                        treatment_signal = self._current_treatment_signal(
                            cached_intelligence, assessed,
                            stale=market_data_stale or processing_delayed,
                        )
                elif signal is not None:
                    # A canonical entry cannot outrun its durable treatment
                    # assignment/link.  Return fail-closed for this event;
                    # the exact immutable publication enables the next event.
                    treatment_policy_pending = True
            if cached_intelligence is None or treatment_policy_pending:
                with _service_stage("decision_intelligence_async_submit", symbol=symbol):
                    self._intelligence_handoff.submit(
                        symbol, intelligence_identity, intelligence_request,
                    )
            if treatment_policy_pending:
                if self._treatment_policy_enabled:
                    downstream_clear_reason = "CLEAR_TREATMENT_PENDING"
                    self._remember_pending_treatment(
                        intelligence_identity, signal, value, account,
                    )
                    performance_diagnostics.record_entry_funnel(
                        symbol, stage="TREATMENT_PENDING", outcome="REJECTED",
                        reason="TREATMENT_PUBLICATION_PENDING",
                        timestamp=value.evaluation_timestamp or observation.timestamp,
                    )
                    signal = None
                else:
                    # Decision intelligence remains observational when its
                    # treatment policy is disabled.  A missing research
                    # publication cannot withhold a canonical PAPER signal.
                    performance_diagnostics.record_entry_funnel(
                        symbol, stage="TREATMENT_DISABLED_NONBLOCKING",
                        outcome="ACCEPTED",
                        timestamp=value.evaluation_timestamp or observation.timestamp,
                    )
        else:
            with _service_stage("decision_intelligence", symbol=symbol):
                if self._decision_intelligence_entry_observer is not None:
                    try:
                        observed = self._decision_intelligence_entry_observer(
                            value=value, candidate=intelligence_candidate, signal=signal,
                            taxonomy_candidate=taxonomy_candidate,
                            legacy_candidate=legacy_candidate,
                            decision_timestamp=value.evaluation_timestamp or observation.timestamp,
                        )
                        intelligence_result = observed[0]
                        treatment_signal = observed[1]
                    except Exception:
                        # Research must never affect the production/PAPER path.
                        pass
                elif self._decision_intelligence_observer is not None:
                    try:
                        intelligence_result = self._decision_intelligence_observer(
                            value=value, candidate=intelligence_candidate, signal=signal,
                            taxonomy_candidate=taxonomy_candidate,
                            legacy_candidate=legacy_candidate,
                        )
                    except Exception:
                        # Research must never affect the production/PAPER path.
                        pass
                    if self._paper_entry_intelligence is not None:
                        try:
                            _entry_intelligence_decision, treatment_signal = self._paper_entry_intelligence(
                                result=intelligence_result, candidate=intelligence_candidate, environment="PAPER",
                                signal_factory=self.runtime.entry_signal,
                                decision_timestamp=value.evaluation_timestamp or observation.timestamp,
                                existing_signal=signal,
                            )
                        except Exception:
                            # Entry intelligence is advisory and fail-closed to the
                            # existing signal path.
                            pass
        if treatment_signal is not None and (
            signal is not None
            or self._decision_intelligence_entry_observer is not None
        ):
            # The production-owned DI/entry callback may authorize an earlier
            # PAPER treatment before the conventional Warrior trigger.  The
            # legacy split callback remains advisory unless a canonical signal
            # already exists.
            signal = treatment_signal
        if intelligence_result is not None and self._pretrigger_shadow is not None:
            try:
                opportunity_id = getattr(intelligence_result, "opportunity_id", None)
                conflicts: list[str] = []
                paper_state = self._paper.get(symbol)
                if paper_state is not None:
                    conflicts.append("PARTIAL_FILL_OR_LIFECYCLE")
                    if paper_state.remaining > 0:
                        conflicts.append("ACTIVE_POSITION")
                if (
                    self._paper_execution_ownership_source is not None
                    and self._paper_execution_ownership_source(symbol)
                ):
                    conflicts.extend(("WORKING_ORDER", "PARTIAL_FILL_OR_LIFECYCLE"))
                if (
                    self._paper_position_quantity_source is not None
                    and self._paper_position_quantity_source(symbol) > ZERO
                ):
                    conflicts.append("ACTIVE_POSITION")
                if opportunity_id is not None:
                    memory = self.opportunity_memory.get(
                        observation.timestamp.date(), symbol, str(opportunity_id),
                    )
                    if memory is not None and memory.order_submitted_at is not None:
                        conflicts.append("DURABLY_CONSUMED")
                self._pretrigger_shadow.observe(
                    value=value, result=intelligence_result,
                    candidate=intelligence_candidate,
                    lifecycle_conflicts=tuple(dict.fromkeys(conflicts)),
                    conventional_signal=conventional_signal,
                )
            except Exception:
                # Phase-A shadow intelligence has no authority over control.
                pass
        if self._try_recovered_continuation_from_observation(
            value, assessed, signal or technical_signal, account, completed,
        ):
            # The recovered path owns both the accepted and rejected new
            # thesis.  Do not let the ordinary entry branch submit the same
            # signal as an initial entry after the seam has evaluated it.
            signal = None
        # Capture the lifecycle's thesis state before the observation-only
        # memory is updated.  A qualified observation must not reopen an
        # opportunity that was explicitly invalidated earlier in this tick.
        pursuit_signal = signal
        pursuit_signal_source = "CURRENT_ENTRY_SIGNAL"
        pursuit_thesis_valid = True
        working_state = self._paper.get(symbol)
        if (
            pursuit_signal is None
            and working_state is not None
            and working_state.remaining > 0
            and self._working_entry_is_active(working_state)
        ):
            pursuit_signal = working_state.signal
            pursuit_signal_source = "RETAINED_WORKING_LIFECYCLE"
            pursuit_thesis_valid = self._working_entry_thesis_valid(working_state)

        memory_signal = signal or technical_signal
        memory_opportunity_id = (
            self._memory_opportunity_ids.get(symbol)
            or (None if memory_signal is None else opportunity_identity(memory_signal))
        )
        if memory_opportunity_id is not None:
            self._remember_memory_identity(symbol, memory_opportunity_id)
            self.opportunity_memory.observe(
                opportunity_id=memory_opportunity_id,
                symbol=symbol,
                trading_date=observation.timestamp.date(),
                observed_at=observation.timestamp,
                price=observation.price,
                qualified=assessed.discovery_qualified,
                setup=(None if assessed.setup is None else assessed.setup.setup_type.value),
                setup_state=(None if assessed.setup is None else assessed.setup.state.value),
                entry_anchor=(None if memory_signal is None else memory_signal.entry_trigger),
                momentum_score=assessed.score.total,
                spread=assessed.spread_percent,
                dollar_volume=assessed.dollar_volume,
                relative_volume=assessed.relative_volume,
                hod=(None if assessed.distance_from_hod_percent is None or assessed.price is None
                     else assessed.price / (Decimal("1") - assessed.distance_from_hod_percent / HUNDRED)),
                vwap_relation=None,
                blocking_reason=(None if not assessed.reason_codes else assessed.reason_codes[-1].value),
            )
        # An authorized entry owns its execution lifecycle after submission.
        # The next Warrior assessment may temporarily have no entry signal
        # while the working order still needs the bounded pursuit checks.  Do
        # not use that transient absence as an execution cancellation signal.
        if (
            pursuit_signal is not None
            and account is not None
            and self._paper_entry_replacer is not None
            and self.config.adaptive_entry.enabled
            and pursuit_signal.symbol in self._paper
        ):
            self._consider_adaptive_entry_replacement(
                value, assessed, pursuit_signal, account,
                thesis_valid=pursuit_thesis_valid,
                signal_source=pursuit_signal_source,
            )
        else:
            performance_diagnostics.record_entry_counter(
                "pursuit_not_invoked_due_to_state"
            )
        if (
            signal is not None
            and account is not None
            and self._paper_entry_rearmer is not None
            and self.config.adaptive_entry.enabled
            and signal.symbol in self._paper
            and self._paper_position_quantity_source is not None
            and self._paper_position_quantity_source(signal.symbol) <= 0
        ):
            self._consider_fast_momentum_rearm(value, assessed, signal, account)
        latched_rejections = entry_rejections(assessed, self.config)
        create_latched_shadow = (
            technical_signal is not None
            and signal is None
            and bool(latched_rejections)
        )
        features = build_features(completed)
        records: list[CaptureRecord] = []
        execution_record: CaptureRecord | None = None
        authorization_decision: PaperEntryAuthorizationDecision | None = None
        entry_value_quantity: int | None = None
        entry_value_signal = signal or technical_signal
        entry_value_state = assessed.status.value
        records.extend(self._evidence_records(value))
        records.append(_discovery_record(value, assessed))
        decision_record = _decision_record(value, assessed, completed, features)
        records.append(decision_record)
        if create_latched_shadow and self._latched_shadow is not None:
            records.extend(self._latched_shadow.create(
                assessed,
                technical_signal,
                value,
                decision_record_id=decision_record.record_id,
                entry_rejections=latched_rejections,
                account=account,
                existing_strategy_position=assessed.symbol in self._paper,
                execution_permitted=self._execution_permitted(),
            ))
        shadow_reasons = list(
            code.value for code in entry_rejections(assessed, self.config)
        )
        already_open = signal is not None and signal.symbol in self._paper
        records.extend(self._transition_records(
            assessed, None if already_open else signal, account=account,
        ))
        records.append(_quality_record(value, completed, self.capture_config))

        if signal is not None:
            if (
                self.config.session_management.enabled
                and entry_cutoff_reached(value.observation.timestamp, self.config.session_management)
            ):
                downstream_clear_reason = "CLEAR_SESSION"
                shadow_reasons.append(ReasonCode.SESSION_ENTRY_CUTOFF.value)
                records.append(_transition_record(
                    assessed, ForwardTransition.ENTRY_BLOCKED,
                    (ReasonCode.SESSION_ENTRY_CUTOFF.value,),
                    ({"gate": "session_entry_cutoff", "passed": False,
                      "observed": value.observation.timestamp.isoformat(),
                      "limit": self.config.session_management.after_hours_entry_cutoff_minutes},),
                ))
                signal = None
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="HALT_SESSION_GATE", outcome="REJECTED",
                    reason="SESSION_ENTRY_CUTOFF",
                    timestamp=value.evaluation_timestamp or observation.timestamp,
                )
        if signal is not None:
            symbol_authorization = _paper_symbol_authorization(signal, account)
            if signal.symbol in self._paper:
                downstream_clear_reason = "CLEAR_LIFECYCLE"
                shadow_reasons.append(ReasonCode.EXECUTION_NOT_ALLOWED.value)
                records.append(_transition_record(
                    assessed, ForwardTransition.ENTRY_BLOCKED,
                    (ReasonCode.EXECUTION_NOT_ALLOWED.value,),
                    ({"gate": "existing_paper_position", "passed": False,
                      "observed": True, "limit": False},),
                ))
                signal = None
            elif not value.halt_state_known:
                downstream_clear_reason = "CLEAR_SESSION"
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="HALT_SESSION_GATE", outcome="REJECTED",
                    reason="HALT_UNKNOWN",
                    timestamp=value.evaluation_timestamp or observation.timestamp,
                )
                shadow_reasons.append(ReasonCode.HALT_UNKNOWN.value)
                blocked = _transition_record(
                    assessed, ForwardTransition.ENTRY_BLOCKED,
                    (ReasonCode.HALT_UNKNOWN.value,),
                    (*_gate_diagnostics(assessed, self.config, account=account),
                     {"gate": "halt_certainty", "passed": False,
                      "observed": "UNKNOWN", "limit": "KNOWN"}),
                )
                records.append(blocked)
                signal = None
            elif account is None:
                downstream_clear_reason = "CLEAR_ACCOUNT"
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="ACCOUNT_GATE", outcome="REJECTED",
                    reason="ACCOUNT_UNAVAILABLE",
                    timestamp=value.evaluation_timestamp or observation.timestamp,
                )
                shadow_reasons.append(ReasonCode.EXECUTION_NOT_ALLOWED.value)
                blocked = _transition_record(
                    assessed, ForwardTransition.ENTRY_BLOCKED,
                    (ReasonCode.EXECUTION_NOT_ALLOWED.value,),
                    _gate_diagnostics(assessed, self.config, account=None),
                )
                records.append(blocked)
                signal = None
            else:
                performance_diagnostics.record_entry_funnel(
                    symbol, stage="ACCOUNT_GATE", outcome="ACCEPTED",
                    timestamp=value.evaluation_timestamp or observation.timestamp,
                )
                # Establish the volatility-adjusted plan before paying up.
                # Execution displacement cannot move that plan's targets.
                signal = adapt_initial_stop(
                    signal, completed, spread_percent=assessed.spread_percent,
                    config=self.config.trade_management,
                    maximum_risk_per_share=self.config.entry.maximum_risk_per_share,
                )
                opportunity_assessment, executable_signal = self._assess_executable_opportunity(
                    value, assessed, signal,
                    diagnostic=lambda reason, **details: performance_diagnostics.record_entry_funnel(
                        symbol, stage="ADAPTIVE_PRICE_GATE", outcome="REJECTED",
                        reason=reason, timestamp=value.evaluation_timestamp or observation.timestamp,
                        setup_type=signal.setup_type.value,
                        lifecycle_id=lifecycle_identity(signal), **details,
                    ),
                )
                opportunity = self.opportunity_engine.apply_assessment(
                    opportunity_assessment
                )
                assessed = self._bind_opportunity_state(assessed, opportunity)
                opportunity_reasons = set(opportunity_assessment.reason_codes)
                if opportunity_reasons:
                    # Adaptive market/timing constraints keep this technical
                    # generation alive. Record every current constraint from
                    # one snapshot instead of allowing serial gate churn to
                    # manufacture a different "rejection" on each tick.
                    downstream_clear_reason = "WAIT_ADAPTIVE_OPPORTUNITY"
                    adaptive_labels: list[str] = []

                    if (
                        OpportunityReason.EXCESSIVE_CURRENT_STRUCTURE_DISPLACEMENT
                        in opportunity_reasons
                    ):
                        adaptive_labels.append("price displacement")
                        performance_diagnostics.record_entry_funnel(
                            symbol, stage="ADAPTIVE_PRICE_GATE", outcome="WAIT",
                            reason="ENTRY_PRICE_DISPLACED",
                            timestamp=value.evaluation_timestamp or observation.timestamp,
                            lifecycle_id=opportunity_assessment.lifecycle_id,
                        )
                        shadow_reasons.append(ReasonCode.ENTRY_PRICE_DISPLACED.value)

                    if OpportunityReason.INSUFFICIENT_CURRENT_REWARD in opportunity_reasons:
                        adaptive_labels.append("remaining reward")
                        performance_diagnostics.record_entry_funnel(
                            symbol, stage="REWARD_GATE", outcome="WAIT",
                            reason="INSUFFICIENT_REMAINING_REWARD",
                            timestamp=value.evaluation_timestamp or observation.timestamp,
                            lifecycle_id=opportunity_assessment.lifecycle_id,
                        )
                        shadow_reasons.append("INSUFFICIENT_REMAINING_REWARD")

                    if OpportunityReason.EXECUTION_QUALITY_WAIT in opportunity_reasons:
                        adaptive_labels.append("execution quality")
                        performance_diagnostics.record_entry_funnel(
                            symbol, stage="EXECUTION_QUALITY", outcome="WAIT",
                            timestamp=value.evaluation_timestamp or observation.timestamp,
                            reason=assessed.execution_block_reason or assessed.execution_quality.value,
                            spread=assessed.spread_percent,
                            lifecycle_id=opportunity_assessment.lifecycle_id,
                        )

                    if (
                        OpportunityReason.EXECUTABLE_TRIGGER_NOT_CONFIRMED
                        in opportunity_reasons
                    ):
                        adaptive_labels.append("trigger confirmation")
                        performance_diagnostics.record_entry_funnel(
                            symbol, stage="EXECUTABLE_TRIGGER_CONFIRMATION",
                            outcome="WAIT",
                            timestamp=value.evaluation_timestamp or observation.timestamp,
                            reason="EXECUTABLE_TRIGGER_NOT_CONFIRMED",
                            bid=observation.bid,
                            trigger=(
                                signal.structural_entry_trigger
                                or signal.entry_trigger
                            ),
                            lifecycle_id=opportunity_assessment.lifecycle_id,
                        )

                    adaptive_reason_codes = []
                    if OpportunityReason.EXECUTION_QUALITY_WAIT in opportunity_reasons:
                        adaptive_reason_codes.append(ReasonCode.EXECUTION_QUALITY_WAIT)
                    if OpportunityReason.INSUFFICIENT_CURRENT_REWARD in opportunity_reasons:
                        adaptive_reason_codes.append(ReasonCode.INSUFFICIENT_REMAINING_REWARD)
                    assessed = replace(
                        assessed,
                        status=CandidateStatus.AWAITING_EXECUTION_DATA,
                        reason_codes=tuple(dict.fromkeys((
                            *assessed.reason_codes,
                            *adaptive_reason_codes,
                        ))),
                        explanations=(
                            *assessed.explanations,
                            "Adaptive opportunity retained; current constraints: "
                            + ", ".join(adaptive_labels)
                            + ". Warrior will reevaluate this same generation while "
                              "Quick Scalper remains independently eligible under shared safety.",
                        ),
                    )
                    # Do not create an ENTRY_BLOCKED transition for soft
                    # constraints. The durable Opportunity Book owns this as
                    # WAITING_EXECUTION, allowing later quotes or another
                    # strategy to act without recreating the opportunity.
                    signal = None
                    position = None
                else:
                    performance_diagnostics.record_entry_funnel(
                        symbol, stage="ADAPTIVE_PRICE_GATE", outcome="ACCEPTED",
                        timestamp=value.evaluation_timestamp or observation.timestamp,
                    )
                    performance_diagnostics.record_entry_funnel(
                        symbol, stage="REWARD_GATE", outcome="ACCEPTED",
                        timestamp=value.evaluation_timestamp or observation.timestamp,
                    )
                    performance_diagnostics.record_entry_funnel(
                        symbol, stage="EXECUTION_QUALITY", outcome="ACCEPTED",
                        timestamp=value.evaluation_timestamp or observation.timestamp,
                        reason=assessed.execution_quality.value,
                        spread=assessed.spread_percent,
                    )
                    signal = executable_signal
                    position = size_position(
                        signal, account_equity=account.equity,
                        buying_power=account.buying_power,
                        allowed_symbols=account.allowed_symbols,
                        existing_exposure=account.existing_exposure,
                        exposure_limit=account.exposure_limit,
                        risk_engine_approved=account.risk_engine_approved,
                        risk_rejection_reason=account.risk_rejection_reason,
                        risk_context={
                            "starting_equity": account.starting_equity,
                            "current_equity": account.current_equity,
                            "campaign_loss_fraction": account.campaign_loss_fraction,
                            "campaign_equity_floor": account.campaign_equity_floor,
                        },
                        broker_restriction=account.broker_restriction,
                        config=self.config.risk,
                        symbol_authorized=symbol_authorization.authorized,
                        diagnostic=lambda reason, details: performance_diagnostics.record_entry_funnel(
                            symbol, stage="RISK_AUTHORIZATION", outcome="REJECTED",
                            reason=reason, timestamp=value.evaluation_timestamp or observation.timestamp,
                            setup_type=signal.setup_type.value,
                            lifecycle_id=lifecycle_identity(signal), **details,
                        ),
                    )
                if signal is not None and position is not None and position.approved:
                    performance_diagnostics.record_entry_funnel(
                        symbol, stage="RISK_AUTHORIZATION", outcome="ACCEPTED",
                        timestamp=value.evaluation_timestamp or observation.timestamp,
                    )
                elif position is not None:
                    downstream_clear_reason = (
                        "CLEAR_ACCOUNT"
                        if ReasonCode.EXECUTION_NOT_ALLOWED in position.reason_codes
                        else "CLEAR_RISK"
                    )
                    hard_reason = (
                        OpportunityReason.ACCOUNT_NOT_AUTHORIZED
                        if ReasonCode.EXECUTION_NOT_ALLOWED in position.reason_codes
                        else OpportunityReason.RISK_NOT_AUTHORIZED
                    )
                    hard_assessment = replace(
                        opportunity_assessment,
                        hard_safety_reason=hard_reason,
                        reason_codes=(hard_reason,),
                    )
                    assessed = self._bind_opportunity_state(
                        assessed,
                        self.opportunity_engine.apply_assessment(hard_assessment),
                    )
                    performance_diagnostics.record_entry_funnel(
                        symbol, stage="RISK_AUTHORIZATION", outcome="REJECTED",
                        reason=(position.reason_codes[-1].value if position.reason_codes else "RISK_REJECTED"),
                        timestamp=value.evaluation_timestamp or observation.timestamp,
                    )
                if position is not None and position.approved:
                    entry_value_quantity = position.shares
                    generation = self._opportunity_generation(signal)
                    ownership_acquired = bool(
                        self.strategy_ownership is None
                        or self.strategy_ownership.acquire(
                            symbol, StrategyOwner.WARRIOR_MOMENTUM, generation,
                        )
                    )
                    if not ownership_acquired:
                        evaluated = self.opportunity_engine.mark_authorization_evaluated(
                            symbol, generation,
                            at=value.evaluation_timestamp or observation.timestamp,
                            authorized=False,
                            reason="SYMBOL_OWNED_BY_OTHER_STRATEGY",
                            risk_result="APPROVED",
                            sizing_result=f"SHARES_{position.shares}",
                        )
                        assessed = self._bind_opportunity_state(assessed, evaluated)
                        performance_diagnostics.record_entry_funnel(
                            symbol, stage="PAPER_AUTHORIZATION", outcome="REJECTED",
                            reason="SYMBOL_OWNED_BY_OTHER_STRATEGY",
                            timestamp=value.evaluation_timestamp or observation.timestamp,
                        )
                        signal = None
                        position = None
                if position is not None and position.approved:
                    entry_records, execution_record, authorization_decision = self._open_paper(
                        signal, position.shares, position.risk_dollars,
                        value.float_provenance, symbol_authorization,
                    )
                    entry_value_state = (
                        authorization_decision.reason.value
                        if authorization_decision is not None
                        else "ENTRY_AUTHORIZATION_ACCEPTED"
                        if entry_records
                        else "ENTRY_AUTHORIZATION_REFUSED"
                    )
                    records.extend(entry_records)
                    generation = self._opportunity_generation(signal)
                    transition_at = value.evaluation_timestamp or observation.timestamp
                    authorization_reason = (
                        authorization_decision.reason.value
                        if authorization_decision is not None
                        else "ENTRY_AUTHORIZATION_ACCEPTED"
                        if entry_records else "ENTRY_AUTHORIZATION_REFUSED"
                    )
                    evaluated = self.opportunity_engine.mark_authorization_evaluated(
                        symbol, generation, at=transition_at,
                        authorized=bool(entry_records),
                        reason=authorization_reason,
                        risk_result="APPROVED",
                        sizing_result=f"SHARES_{position.shares}",
                    )
                    assessed = self._bind_opportunity_state(assessed, evaluated)
                    records.append(_opportunity_authorization_record(
                        opportunity_assessment,
                        authorized=bool(entry_records),
                        reason=authorization_reason,
                        risk_result="APPROVED",
                        sizing_result=f"SHARES_{position.shares}",
                    ))
                    if execution_record is not None:
                        records.append(execution_record)
                    if not entry_records:
                        downstream_clear_reason = "CLEAR_ACCOUNT"
                        if self.strategy_ownership is not None:
                            self.strategy_ownership.release(
                                symbol, StrategyOwner.WARRIOR_MOMENTUM,
                                generation,
                            )
                    performance_diagnostics.record_entry_funnel(
                        symbol, stage="PAPER_AUTHORIZATION",
                        outcome="ACCEPTED" if entry_records else "REJECTED",
                        timestamp=value.evaluation_timestamp or observation.timestamp,
                        reason=(None if entry_records else "PAPER_SUBMITTER_REJECTED"),
                    )
                    if entry_records:
                        self.opportunity_engine.mark_authorized(
                            symbol, generation, at=transition_at,
                        )
                        pending = self.opportunity_engine.mark_order_intent(
                            symbol, generation, at=transition_at, submitted=True,
                        )
                        assessed = self._bind_opportunity_state(assessed, pending)
                        performance_diagnostics.record_entry_funnel(
                            symbol, stage="ORDER_INTENT", timestamp=value.evaluation_timestamp or observation.timestamp,
                        )
                        self._record_setup_lifecycle(
                            value, assessed, setup_state="ENTRY_AUTHORIZED",
                            technical_signal=technical_signal, signal=signal,
                            timestamp=value.evaluation_timestamp or observation.timestamp,
                            lifecycle_stage="ENTRY_AUTHORIZED",
                        )
                        self._record_setup_lifecycle(
                            value, assessed, setup_state="ORDER_SUBMITTED",
                            technical_signal=technical_signal, signal=signal,
                            timestamp=value.evaluation_timestamp or observation.timestamp,
                            lifecycle_stage="ORDER_SUBMITTED",
                        )
                    if entry_records:
                        from app.trade_intelligence.opportunity_memory import EntryAttemptSummary
                        self.opportunity_memory.record_entry_attempt(
                            observation.timestamp.date(), symbol,
                            memory_opportunity_id,
                            EntryAttemptSummary(
                                lifecycle_id=lifecycle_identity(signal),
                                setup=signal.setup_type.value,
                                attempted_at=signal.timestamp,
                                requested_price=signal.entry_trigger,
                                requested_quantity=Decimal(position.shares),
                                structural_stop=signal.stop_price,
                                spread=signal.spread_percent,
                                liquidity=signal.dollar_volume,
                                risk=position.risk_dollars,
                                result="ATTEMPTED",
                            ),
                        )
                    # A configured execution bridge is authoritative for the
                    # entry boundary.  If it rejects the command, do not
                    # return an apparently executable signal to callers.
                    if self._paper_entry_submitter is not None and not entry_records:
                        shadow_reasons.append(ReasonCode.EXECUTION_NOT_ALLOWED.value)
                        signal = None
                        performance_diagnostics.record_entry_funnel(
                            symbol, stage="ORDER_REJECTED", outcome="REJECTED",
                            reason="EXECUTION_NOT_ALLOWED",
                            timestamp=value.evaluation_timestamp or observation.timestamp,
                        )
                elif position is not None:
                    shadow_reasons.extend(code.value for code in position.reason_codes)
                    records.append(_transition_record(
                        assessed, ForwardTransition.ENTRY_BLOCKED,
                        tuple(code.value for code in position.reason_codes),
                        (*_gate_diagnostics(assessed, self.config, account=account),
                         *_account_gate_diagnostics(signal, account)),
                    ))
                    signal = None

        if signal is None and self._shadow is not None:
            records.append(self._shadow.observe_rejection(
                decision_record, value, assessed,
                tuple(dict.fromkeys(shadow_reasons)),
                scanner_rank=value.scanner_rank,
                scanner_score=value.scanner_score,
                scanner_classification=value.scanner_classification,
                scanner_failed_rules=value.scanner_failed_rules,
            ))

        if (
            technical_signal is not None
            and self._paper_entry_submitter is not None
            and execution_record is None
        ):
            existing_execution_reason = None
            if technical_signal.symbol in self._paper:
                existing_execution_reason = (
                    PaperEntryAuthorizationReason.WORKING_ORDER_EXISTS
                    if self._working_entry_is_active(self._paper[technical_signal.symbol])
                    else PaperEntryAuthorizationReason.POSITION_EXISTS
                )
            try:
                execution_record = _prebridge_execution_gate_record(
                    assessed, technical_signal, value, account,
                    config=self.config,
                    stale_after=self.capture_config.quote_stale_after_seconds,
                    execution_permitted=self._execution_permitted(),
                    existing_execution_reason=existing_execution_reason,
                )
            except Exception:
                execution_record = None
            if execution_record is not None:
                records.append(execution_record)

        setup = assessed.setup
        if (
            setup is not None and setup.state is SetupState.TRIGGERED
            and assessed.score.total >= self.config.entry.minimum_momentum_score
            and signal is None and assessed.symbol not in self._paper
            and setup.trigger is not None and setup.stop_price is not None
        ):
            records.extend(self._start_counterfactual(assessed))
        observer = self._entry_value_observer
        if (
            callable(observer)
            and entry_value_signal is not None
            and entry_value_quantity is not None
        ):
            try:
                observer(
                    value=value,
                    candidate=assessed,
                    signal=entry_value_signal,
                    planned_quantity=entry_value_quantity,
                    decision_state=entry_value_state,
                    lifecycle_id=lifecycle_identity(entry_value_signal),
                )
            except Exception:
                # EOV is a one-way research consumer. It cannot change this
                # decision, record publication, or order outcome.
                pass
        with _service_stage("capture_record_submission", symbol=symbol):
            self._submit_records(tuple(records))
        if technical_signal is not None and signal is None:
            performance_diagnostics.record_entry_funnel(
                symbol, stage="TECHNICAL_SIGNAL_CLEARED", outcome="REJECTED",
                reason=(downstream_clear_reason or "CLEAR_LIFECYCLE"),
                timestamp=value.evaluation_timestamp or observation.timestamp,
            )
        return assessed, signal

    def observe_paper_event(self, event: object) -> None:
        """Anchor prospective path capture to an authoritative PAPER fill."""
        fill = getattr(event, "fill", None)
        order = getattr(event, "order", None)
        lifecycle = str(getattr(order, "lifecycle_id", None) or "").strip()
        campaign = str(self.paper_campaign_id or "").strip()
        if fill is None or not lifecycle or not campaign:
            return

        symbol = str(getattr(fill, "symbol", "")).strip().upper()
        side = str(getattr(fill, "side", "")).strip().upper()
        quantity = Decimal(str(getattr(fill, "quantity", "0")))
        timestamp = getattr(fill, "timestamp", None)
        price = Decimal(str(getattr(fill, "fill_price", "0")))
        if not symbol or side not in {"BUY", "SELL"} or quantity <= 0 or timestamp is None:
            return
        if side == "BUY":
            self.opportunity_engine.mark_entered(symbol, lifecycle, at=timestamp)
        key = campaign, lifecycle
        state = self._execution_paths.get(key)
        if state is None and side == "BUY":
            if len(self._execution_paths) >= self._execution_path_max_active:
                self._submit_records((CaptureRecord.create(
                    CaptureRecordType.EXECUTION_PRICE_PATH, symbol, timestamp,
                    {"action": "CAPTURE_LIMIT_REACHED", "paper_campaign_id": campaign,
                     "lifecycle_id": lifecycle, "limit": self._execution_path_max_active},
                    identity_parts=(campaign, lifecycle, "active-limit"),
                ),))
                return
            state = {
                "symbol": symbol, "quantity": Decimal("0"), "samples": 0,
                "last_sample_at": timestamp, "path_started_at": timestamp,
                "missing_intervals": 0, "stale_quotes": 0,
                "sample_limit_recorded": False,
            }
            self._execution_paths[key] = state
        if state is None:
            # A sell without a locally observed/recovered entry remains
            # explicit; it is never guessed into another lifecycle.
            self._submit_records((CaptureRecord.create(
                CaptureRecordType.EXECUTION_PRICE_PATH, symbol, timestamp,
                {"action": "UNMATCHED_FILL", "paper_campaign_id": campaign,
                 "lifecycle_id": lifecycle, "side": side, "quantity": quantity,
                 "price": price, "reason": "NO_ACTIVE_AUTHORITATIVE_PATH"},
                identity_parts=(campaign, lifecycle, str(getattr(event, "sequence", ""))),
            ),))
            return
        state["quantity"] = Decimal(str(state["quantity"])) + (
            quantity if side == "BUY" else -quantity
        )
        payload = {
            "action": "FILL", "paper_campaign_id": campaign,
            "lifecycle_id": lifecycle, "order_id": getattr(order, "order_id", None),
            "fill_event_id": f"{getattr(event, 'source', '')}:{getattr(event, 'sequence', '')}",
            "side": side, "quantity": quantity, "price": price,
            "fill_timestamp": timestamp,
            "remaining_quantity": state["quantity"],
        }
        self._submit_records((CaptureRecord.create(
            CaptureRecordType.EXECUTION_PRICE_PATH, symbol, timestamp, payload,
            identity_parts=(campaign, lifecycle, payload["fill_event_id"]),
        ),))
        if Decimal(str(state["quantity"])) <= 0:
            self._execution_paths.pop(key, None)
            if (
                self.strategy_ownership is not None
                and not (
                    self._paper_execution_ownership_source is not None
                    and self._paper_execution_ownership_source(symbol)
                )
            ):
                self.strategy_ownership.release(
                    symbol, StrategyOwner.WARRIOR_MOMENTUM, lifecycle,
                )

    def _record_setup_lifecycle(
        self,
        value: PointInTimeObservation,
        candidate: MomentumCandidate,
        *,
        setup_state: str,
        technical_signal: MomentumEntrySignal | None,
        signal: MomentumEntrySignal | None,
        timestamp: datetime,
        lifecycle_stage: str | None = None,
    ) -> None:
        """Maintain bounded transition-only setup timing evidence."""
        symbol = candidate.symbol.strip().upper()
        if not symbol:
            return
        record = self._setup_lifecycles.get(symbol)
        if record is None:
            if len(self._setup_lifecycles) >= self._setup_lifecycle_capacity:
                self._setup_lifecycles.popitem(last=False)
            record = {"symbol": symbol}
            self._setup_lifecycles[symbol] = record
        self._setup_lifecycles.move_to_end(symbol)
        observation = value.observation
        price = observation.price
        hod = None
        if (
            price is not None
            and candidate.distance_from_hod_percent is not None
            and candidate.distance_from_hod_percent < Decimal("100")
        ):
            hod = price / (Decimal("1") - candidate.distance_from_hod_percent / HUNDRED)
        setup = candidate.setup
        if setup is None:
            setup_reason = (
                "SETUP_WAITING_FOR_HISTORY"
                if len(value.bars) < 5 else "SETUP_WAITING_FOR_STRUCTURE"
            )
        elif setup.state is SetupState.FORMING:
            setup_reason = "SETUP_WAITING_FOR_BREAKOUT"
        elif setup.state is SetupState.TRIGGERED:
            setup_reason = "SETUP_TRIGGERED"
        else:
            setup_reason = "SETUP_STRUCTURALLY_INVALID"
        changed: dict[str, object] = {}
        price_fields = {
            "first_discovered_at": "price_at_first_discovery",
            "first_observed_at": "price_at_first_observation",
            "first_scanner_qualified_at": "price_at_first_qualification",
            "first_setup_forming_at": "price_at_first_forming",
            "first_setup_triggered_at": "price_at_first_trigger",
            "first_order_submitted_at": "price_at_order_submission",
            "first_execution_eligible_at": "price_at_execution_eligibility",
            "first_entry_authorized_at": "price_at_entry_authorization",
        }

        def first(field: str, field_price: Decimal | None = price) -> None:
            if record.get(field) is None:
                record[field] = timestamp
                changed[field] = timestamp
                price_field = price_fields.get(field)
                if (
                    field_price is not None
                    and price_field is not None
                    and price_field not in record
                ):
                    record[price_field] = field_price
                    changed[price_field] = field_price

        first("first_discovered_at")
        first("first_observed_at")
        first("first_scanner_assessed_at")
        first("first_warrior_evaluated_at")
        if candidate.discovery_qualified:
            first("first_scanner_qualified_at")
        normalized_state = str(setup_state).upper()
        if normalized_state in {"NO_SETUP", SetupState.NOT_FORMED.value, SetupState.UNKNOWN.value}:
            first("first_setup_none_at")
        elif normalized_state == SetupState.FORMING.value:
            first("first_setup_forming_at")
        elif normalized_state == SetupState.TRIGGERED.value:
            first("first_setup_triggered_at")
            if hod is not None and "hod_at_trigger" not in record:
                record["hod_at_trigger"] = hod
        if technical_signal is not None:
            first("first_technical_signal_at")
        if signal is not None:
            first("first_execution_eligible_at")
        if lifecycle_stage in {"ENTRY_AUTHORIZED", "ORDER_SUBMITTED"}:
            first("first_entry_authorized_at")
            if hod is not None and "hod_at_entry" not in record:
                record["hod_at_entry"] = hod
            record["actual_order_price"] = price
        if lifecycle_stage == "ORDER_SUBMITTED":
            first("first_order_submitted_at")
        for field, field_value in (
            ("last_observation_at", timestamp),
            ("session", candidate.session),
            ("current_setup_type", None if setup is None else setup.setup_type.value),
            ("trigger_price", None if setup is None else setup.trigger),
            ("structural_stop", None if setup is None else setup.stop_price),
            ("hod", hod),
            ("source_identity", None if setup is None else setup.structural_episode_id),
            ("setup_development_reason", setup_reason),
        ):
            if field == "last_observation_at":
                if field not in record:
                    changed[field] = field_value
                record[field] = field_value
            elif field == "hod":
                # HOD is continuously changing market context, not a
                # lifecycle transition. Keep it in the read model without
                # emitting a diagnostic record for every update.
                if field_value is not None:
                    record[field] = field_value
            elif field_value is not None and record.get(field) != field_value:
                record[field] = field_value
                changed[field] = field_value
        if lifecycle_stage is not None:
            record["last_transition"] = lifecycle_stage
            changed["last_transition"] = lifecycle_stage
        if not changed or self.writer is None:
            return
        payload = dict(record)
        payload["transition"] = lifecycle_stage or normalized_state
        payload["changed_fields"] = tuple(sorted(changed))
        try:
            self.writer.submit_diagnostic(CaptureRecord.create(
                CaptureRecordType.SETUP_LIFECYCLE, symbol, timestamp, payload,
                identity_parts=(str(payload["transition"]), ",".join(sorted(changed))),
            ))
        except Exception:
            return

    def setup_lifecycle_snapshot(
        self, symbol: str | None = None,
    ) -> tuple[dict[str, object], ...] | dict[str, object] | None:
        """Return a bounded copy of setup lifecycle timing evidence."""
        if symbol is not None:
            value = self._setup_lifecycles.get(symbol.strip().upper())
            return None if value is None else dict(value)
        return tuple(dict(value) for value in self._setup_lifecycles.values())

    def restore_execution_lifecycles(self, orders: Iterable[object]) -> None:
        """Resume open paths without pretending the pre-restart path exists."""
        campaign = str(self.paper_campaign_id or "").strip()
        grouped: dict[str, list[tuple[datetime, str, Decimal, str]]] = {}
        for order in orders:
            request = getattr(order, "request", None)
            lifecycle = str(getattr(request, "strategy_lifecycle_id", None) or "").strip()
            if not lifecycle:
                continue
            side = str(getattr(getattr(request, "side", None), "value", getattr(request, "side", ""))).upper()
            symbol = str(getattr(request, "symbol", "")).strip().upper()
            for fill in getattr(order, "fills", ()):
                grouped.setdefault(lifecycle, []).append((
                    fill.timestamp, side, Decimal(str(fill.quantity)), symbol,
                ))
        for lifecycle, fills in grouped.items():
            fills.sort(key=lambda item: item[0])
            quantity = sum((qty if side == "BUY" else -qty for _, side, qty, _ in fills), Decimal("0"))
            if quantity <= 0 or len(self._execution_paths) >= self._execution_path_max_active:
                continue
            timestamp, _, _, symbol = fills[-1]
            key = campaign, lifecycle
            self._execution_paths[key] = {
                "symbol": symbol, "quantity": quantity, "samples": 0,
                "last_sample_at": None, "path_started_at": timestamp,
                "missing_intervals": 0, "stale_quotes": 0,
                "sample_limit_recorded": False,
            }
            self._submit_records((CaptureRecord.create(
                CaptureRecordType.EXECUTION_PRICE_PATH, symbol, timestamp,
                {"action": "RECOVERY", "paper_campaign_id": campaign,
                 "lifecycle_id": lifecycle, "remaining_quantity": quantity,
                 "historical_path": "UNAVAILABLE_BEFORE_RESTART",
                 "prospective_capture_starts_at": None,
                 "recovered_through_fill_timestamp": timestamp},
                identity_parts=(campaign, lifecycle, "recovery", timestamp.isoformat()),
            ),))

    def _observe_execution_price_path(self, value: PointInTimeObservation) -> None:
        observation = value.observation
        timestamp = value.quote_observed_at or observation.timestamp
        now = value.evaluation_timestamp or observation.timestamp
        self.observe_execution_quote(
            symbol=observation.symbol, quote_timestamp=timestamp,
            evaluated_at=now, bid=observation.bid, ask=observation.ask,
            last=observation.price,
        )

    def observe_execution_quote(
        self, *, symbol: str, quote_timestamp: datetime,
        evaluated_at: datetime, bid: Decimal | None, ask: Decimal | None,
        last: Decimal | None,
    ) -> None:
        """Sample owned PAPER paths independently of strategy evaluation cadence."""
        symbol = symbol.strip().upper()
        timestamp, now = quote_timestamp, evaluated_at
        for (campaign, lifecycle), state in tuple(self._execution_paths.items()):
            if state["symbol"] != symbol:
                continue
            previous_sample = state["last_sample_at"]
            if previous_sample is not None and (timestamp - previous_sample).total_seconds() < self._execution_path_min_interval_seconds:
                continue
            samples = int(state["samples"])
            if samples >= self._execution_path_max_samples:
                if not bool(state["sample_limit_recorded"]):
                    state["sample_limit_recorded"] = True
                    self._submit_records((CaptureRecord.create(
                        CaptureRecordType.EXECUTION_PRICE_PATH,
                        symbol, now,
                        {"action": "CAPTURE_LIMIT_REACHED",
                         "paper_campaign_id": campaign,
                         "lifecycle_id": lifecycle,
                         "limit": self._execution_path_max_samples},
                        identity_parts=(campaign, lifecycle, "sample-limit"),
                    ),))
                continue
            gap_seconds = None if previous_sample is None else max(0.0, (timestamp - previous_sample).total_seconds())
            stale_seconds = max(0.0, (now - timestamp).total_seconds())
            missing = gap_seconds is not None and gap_seconds > self._execution_path_gap_seconds
            stale = stale_seconds > float(self.capture_config.quote_stale_after_seconds)
            state["samples"] = samples + 1
            state["last_sample_at"] = timestamp
            state["missing_intervals"] = int(state["missing_intervals"]) + int(missing)
            state["stale_quotes"] = int(state["stale_quotes"]) + int(stale)
            midpoint = None
            if bid is not None and ask is not None:
                midpoint = (bid + ask) / Decimal("2")
            self._submit_records((CaptureRecord.create(
                CaptureRecordType.EXECUTION_PRICE_PATH, symbol, now,
                {"action": "QUOTE", "paper_campaign_id": campaign,
                 "lifecycle_id": lifecycle, "quote_timestamp": timestamp,
                 "bid": bid, "ask": ask,
                 "midpoint": midpoint, "last": last,
                 "quote_age_seconds": Decimal(str(stale_seconds)),
                 "future_quote": timestamp > now,
                 "stale_quote": stale, "interval_seconds": gap_seconds,
                 "missing_interval": missing, "sample_number": samples + 1},
                identity_parts=(campaign, lifecycle, timestamp.isoformat()),
            ),))

    def _remember_memory_identity(self, symbol: str, opportunity_id: str) -> None:
        """Retain one latest identity per symbol with deterministic eviction."""
        normalized = symbol.strip().upper()
        self._memory_opportunity_ids[normalized] = opportunity_id
        self._memory_opportunity_ids.move_to_end(normalized)
        geometry = self._memory_geometry_keys.get(normalized)
        if geometry is not None:
            self._memory_geometry_keys.move_to_end(normalized)
        limit = self.opportunity_memory.max_active
        while len(self._memory_opportunity_ids) > limit:
            evicted, _ = self._memory_opportunity_ids.popitem(last=False)
            self._memory_geometry_keys.pop(evicted, None)

    def _reconcile_structural_opportunity(
        self, symbol: str, signal: MomentumEntrySignal,
    ) -> str:
        """Select the current structural opportunity without using a timer.

        ``opportunity_identity`` is supplied by normalized discovery or the
        detector-owned legacy episode.  Executable prices are evidence on that
        identity, not a second identity system.
        """
        current = opportunity_identity(signal)
        previous = self._memory_opportunity_ids.get(symbol)
        prior_geometry = self._memory_geometry_keys.get(symbol)
        if previous is None:
            self._remember_memory_identity(symbol, current)
            self._memory_geometry_keys[symbol] = current
            return current
        if prior_geometry is None:
            # A restored/taxonomy-owned ID has no local geometry authority.
            # Preserve it until the owning detector publishes a replacement
            # identity rather than guessing from a synthetic test signal.
            self._memory_geometry_keys[symbol] = current
            self._memory_geometry_keys.move_to_end(symbol)
            return previous
        if prior_geometry == current:
            self._memory_opportunity_ids.move_to_end(symbol)
            self._memory_geometry_keys.move_to_end(symbol)
            return previous

        state = self._paper.get(symbol)
        working = bool(
            state is not None
            and state.remaining > 0
            and self._working_entry_is_active(state)
        )
        positioned = bool(
            self._paper_position_quantity_source is not None
            and self._paper_position_quantity_source(symbol) > ZERO
        )
        if working or positioned:
            # Working-entry and position authorities remain stronger than a
            # newly observed membership.  A terminal in-memory state alone is
            # not an authority and must not impose a reentry cooldown.
            return previous

        at = signal.timestamp
        self.opportunity_memory.invalidate(
            at.date(), symbol, previous, at, "STRUCTURE_SUPERSEDED",
        )
        self._remember_memory_identity(symbol, current)
        self._memory_geometry_keys[symbol] = current
        self._memory_geometry_keys.move_to_end(symbol)
        return current

    def _try_recovered_continuation_from_observation(
        self,
        value: PointInTimeObservation,
        candidate: MomentumCandidate,
        signal: MomentumEntrySignal | None,
        account: PaperAccountContext | None,
        completed: tuple[MinuteBar, ...],
    ) -> bool:
        """Route an already-established continuation through the Phase 4 seam.

        The ordinary Warrior detectors remain authoritative.  This branch is
        entered only when they produce a current signal (or a technical signal
        retained behind an execution block) whose anchor differs from the
        remembered entry attempt, and the memory record already contains a
        prior impulse/pullback.  Thus completed/history-derived setup context
        is required; a live quote cannot create a recovered structure.
        """
        if signal is None:
            return False
        opportunity_id = (
            self._memory_opportunity_ids.get(signal.symbol)
            or opportunity_identity(signal)
        )
        record = self.opportunity_memory.get(
            signal.timestamp.date(), signal.symbol, opportunity_id,
        )
        if (
            record is None
            or not record.attempts
            or record.original_entry_anchor is None
            or record.original_entry_anchor == signal.entry_trigger
            or record.post_peak_pullback_low is None
        ):
            return False

        # A triggered setup is derived from completed bars by
        # WarriorMomentumRuntime.discover().  Persist that fact before using
        # the fresh observation as the final trigger.
        if candidate.setup is None or candidate.setup.state is not SetupState.TRIGGERED:
            return False
        if completed:
            self.opportunity_memory.record_structure_established(
                signal.timestamp.date(), signal.symbol, opportunity_id,
                completed[-1].timestamp,
            )
        if record.structure_established_at is None:
            from app.trade_intelligence.opportunity_memory import OpportunityTransitionType
            self.opportunity_memory.record_transition(
                signal.timestamp.date(), signal.symbol, opportunity_id,
                signal.timestamp, OpportunityTransitionType.ENTRY_CANCELLED,
                reason="STRUCTURE_NOT_ESTABLISHED",
            )
            return True

        if account is None:
            from app.trade_intelligence.opportunity_memory import OpportunityTransitionType
            self.opportunity_memory.record_transition(
                signal.timestamp.date(), signal.symbol, opportunity_id,
                signal.timestamp, OpportunityTransitionType.ENTRY_CANCELLED,
                reason="ACCOUNT_UNAVAILABLE",
            )
            return True
        stale_after = self.capture_config.quote_stale_after_seconds
        ages = (
            value.quote_freshness_seconds,
            value.last_price_freshness_seconds,
        )
        freshness_ok = all(
            age is not None and age >= ZERO and age <= stale_after
            for age in ages
        )
        setup_type = candidate.setup.setup_type
        from app.trade_intelligence.opportunity_memory import PullbackClassification
        if setup_type.value in {"MICRO_PULLBACK", "BULL_FLAG", "MOMENTUM_REACCELERATION"}:
            higher_low, reclaim = True, False
        elif setup_type.value == "RECLAIM_CONTINUATION":
            higher_low, reclaim = False, True
        else:
            higher_low, reclaim = False, True
        classification = PullbackClassification.HEALTHY.value
        self.authorize_recovered_continuation(
            candidate, signal, account,
            structure=setup_type.value,
            classification=classification,
            structure_established_at=record.structure_established_at,
            live_price=value.observation.price,
            trigger_price=signal.entry_trigger,
            reclaim_level=(candidate.setup.resistance if reclaim else None),
            higher_low=higher_low, reclaim=reclaim,
            momentum_reaccelerated=setup_type.value in {
                "MOMENTUM_ACCELERATION", "MOMENTUM_REACCELERATION",
            },
            working_entry=False,
            freshness_ok=freshness_ok,
        )
        # Whether accepted or blocked, this was a recovered candidate and
        # must not fall through to ordinary initial-entry submission.
        return True

    def _working_entry_thesis_valid(self, state: _PaperState) -> bool:
        """Return whether an active opportunity explicitly invalidated itself.

        A missing current signal is intentionally not invalidation.  Only the
        observation-only opportunity memory's terminal states can explicitly
        end a retained working-entry thesis here; the execution bridge still
        owns order-terminal checks and all replacement policy gates.
        """
        from app.trade_intelligence.opportunity_memory import (
            OpportunityMemoryState,
        )

        opportunity_id = (
            self._memory_opportunity_ids.get(state.signal.symbol)
            or opportunity_identity(state.signal)
        )
        record = self.opportunity_memory.get(
            state.signal.timestamp.date(), state.signal.symbol, opportunity_id,
        )
        return record is None or record.opportunity_state not in {
            OpportunityMemoryState.INVALIDATED,
            OpportunityMemoryState.CLOSED,
        }

    def _working_entry_is_active(self, state: _PaperState) -> bool:
        """Ask the execution owner whether this lifecycle still has an entry."""
        if self._paper_working_entry_source is None:
            # Standalone analytical callers do not expose order ownership;
            # preserve their existing in-memory lifecycle behavior.
            return state.remaining > 0
        try:
            return bool(self._paper_working_entry_source(
                state.signal.symbol, lifecycle_identity(state.signal),
            ))
        except Exception:
            return False

    def _execution_entry_signal(
        self,
        value: PointInTimeObservation,
        candidate: MomentumCandidate,
        signal: MomentumEntrySignal,
        diagnostic=None,
    ) -> MomentumEntrySignal | None:
        """Bind a structural trigger to a currently executable PAPER limit.

        The detector-owned trigger remains immutable evidence.  A fresh ask
        inside the existing adaptive displacement envelope may become the
        executable limit; an ask outside that envelope is a missed entry, not
        permission to submit a stale passive order.
        """
        symbol = candidate.symbol.strip().upper()
        lifecycle = self._setup_lifecycles.get(symbol, {})
        evaluated_at = value.evaluation_timestamp or value.observation.timestamp
        structural = signal.structural_entry_trigger or signal.entry_trigger
        displacement_percent = self.runtime.execution_displacement_percent(candidate)
        absolute_outer_limit = Decimal("0.50")
        maximum_percent_price = structural * (Decimal("1") + displacement_percent / HUNDRED)
        maximum_absolute_price = structural + absolute_outer_limit
        maximum = min(maximum_percent_price, maximum_absolute_price)

        def _age(timestamp):
            try:
                if timestamp is None or evaluated_at is None:
                    return None
                return max(Decimal("0"), Decimal(str((evaluated_at - timestamp).total_seconds())))
            except Exception:
                return None

        def _continuation_class():
            setup_name = signal.setup_type.value
            if setup_name in {"MOMENTUM_REACCELERATION"}:
                return "REACCELERATION"
            if setup_name == "RECLAIM_CONTINUATION":
                return "RECLAIM"
            if setup_name in {"MICRO_PULLBACK", "BULL_FLAG"}:
                return "CONTINUATION"
            if setup_name in {"HIGH_OF_DAY_BREAKOUT", "FLAT_TOP_BREAKOUT", "MOMENTUM_ACCELERATION"}:
                return "INITIAL_BREAKOUT"
            return "OTHER"

        def _actual_displacement(price):
            try:
                return (Decimal(price) - structural) / structural * HUNDRED
            except Exception:
                return None

        def _publish_price_gate(result: str, *, proposed=None, displacement_percent_value=None,
                                displacement_dollars=None, effective_maximum=None) -> None:
            try:
                entry_price = proposed if proposed is not None else (Decimal(ask) if ask is not None else None)
                spread = None
                if value.observation.bid is not None and ask is not None:
                    spread = Decimal(ask) - Decimal(value.observation.bid)
                risk = signal.stop_price
                risk_per_share = signal.risk_per_share
                first_r = final_r = None
                if entry_price is not None and signal.target_levels and risk_per_share is not None:
                    effective_risk = entry_price - risk
                    if spread is not None:
                        effective_risk += spread
                    if effective_risk > ZERO and len(signal.target_levels) >= 2:
                        first_r = (signal.target_levels[0] - entry_price - (spread or ZERO)) / effective_risk
                        final_r = (signal.target_levels[-1] - entry_price - (spread or ZERO)) / effective_risk
                assessment = lifecycle.get("entry_extension")
                performance_diagnostics.record_price_gate_sample(
                    symbol=symbol, setup_type=signal.setup_type.value,
                    lifecycle_id=lifecycle_identity(signal), evaluation_timestamp=evaluated_at,
                    structural_trigger=structural,
                    trigger_timestamp=lifecycle.get("first_setup_triggered_at"),
                    trigger_age_seconds=_age(lifecycle.get("first_setup_triggered_at")),
                    entry_ready_timestamp=lifecycle.get("first_execution_eligible_at"),
                    entry_ready_age_seconds=_age(lifecycle.get("first_execution_eligible_at")),
                    technical_signal_timestamp=lifecycle.get("first_technical_signal_at") or evaluated_at,
                    signal_age_seconds=_age(lifecycle.get("first_technical_signal_at") or evaluated_at),
                    ask=ask, bid=value.observation.bid, spread=spread,
                    reference_price=signal.reference_price, structural_stop=signal.stop_price,
                    base_displacement_percent=self.config.adaptive_entry.max_displacement_percent,
                    contextual_displacement_percent=displacement_percent,
                    effective_percent_limit=maximum_percent_price,
                    absolute_outer_limit=absolute_outer_limit,
                    effective_max_execution_price=effective_maximum if effective_maximum is not None else maximum,
                    actual_displacement_percent=displacement_percent_value,
                    actual_displacement_dollars=displacement_dollars,
                    risk_per_share=risk_per_share,
                    risk_normalized_extension=None if assessment is None else assessment.risk_normalized_extension,
                    remaining_first_target_r=first_r, remaining_final_target_r=final_r,
                    continuation_class=_continuation_class(), gate_result=result,
                )
            except Exception:
                return

        ask = value.observation.ask
        if ask is None or ask <= ZERO:
            _publish_price_gate("ENTRY_PRICE_INVALID")
            if diagnostic is not None:
                diagnostic(
                    "ENTRY_PRICE_INVALID", structural=structural,
                    bid=value.observation.bid, ask=ask,
                    proposed_execution_price=ask,
                    maximum_percent_price=None, maximum_absolute_price=None,
                    effective_maximum=None, displacement_percent=None,
                    displacement_absolute=None,
                )
            return None
        self._record_entry_extension(
            value, candidate, signal, entry_price=Decimal(ask),
            displacement_limit=maximum,
        )
        # If the live ask is already at/below the canonical trigger, preserve
        # the detector-owned limit.  The bounded displacement policy applies
        # only when execution would require paying above that trigger.
        if Decimal(ask) <= structural:
            _publish_price_gate(
                "ACCEPTED", proposed=Decimal(ask),
                displacement_percent_value=_actual_displacement(ask),
                displacement_dollars=Decimal(ask) - structural, effective_maximum=maximum,
            )
            return signal
        executable = max(structural, Decimal(ask))
        if executable > maximum:
            percent_limit = maximum_percent_price
            absolute_limit = maximum_absolute_price
            percent_failed = executable > percent_limit
            absolute_failed = executable > absolute_limit
            subtype = (
                "ENTRY_PRICE_DISPLACED_BOTH" if percent_failed and absolute_failed
                else "ENTRY_PRICE_DISPLACED_PERCENT" if percent_failed
                else "ENTRY_PRICE_DISPLACED_ABSOLUTE"
            )
            _publish_price_gate(
                subtype, proposed=executable,
                displacement_percent_value=_actual_displacement(executable),
                displacement_dollars=executable - structural, effective_maximum=maximum,
            )
            if diagnostic is not None:
                diagnostic(subtype, structural=structural, bid=value.observation.bid,
                           ask=ask, proposed_execution_price=executable,
                           maximum_percent_price=percent_limit,
                           maximum_absolute_price=absolute_limit, effective_maximum=maximum,
                           displacement_percent=(executable - structural) / structural * HUNDRED,
                           displacement_absolute=executable - structural)
            return None
        if executable <= signal.stop_price:
            _publish_price_gate(
                "ENTRY_PRICE_BELOW_STOP", proposed=executable,
                displacement_percent_value=_actual_displacement(executable),
                displacement_dollars=executable - structural, effective_maximum=maximum,
            )
            if diagnostic is not None:
                diagnostic(
                    "ENTRY_PRICE_BELOW_STOP", structural=structural,
                    bid=value.observation.bid, ask=ask,
                    proposed_execution_price=executable,
                    maximum_percent_price=maximum_percent_price,
                    maximum_absolute_price=maximum_absolute_price,
                    effective_maximum=maximum,
                    displacement_percent=(executable - structural) / structural * HUNDRED,
                    displacement_absolute=executable - structural,
                )
            return None
        if executable == signal.entry_trigger:
            _publish_price_gate(
                "ACCEPTED", proposed=executable,
                displacement_percent_value=_actual_displacement(executable),
                displacement_dollars=executable - structural, effective_maximum=maximum,
            )
            return signal
        risk = executable - signal.stop_price
        if risk <= ZERO:
            return None
        # The canonical setup already passed the strategy's structural
        # risk-per-share gate.  Moving the executable limit to a fresh ask
        # inside the existing displacement envelope must not reclassify that
        # setup as structurally invalid.  Position sizing below uses this
        # actual executable risk, so the account risk budget remains
        # authoritative without turning a few cents of execution displacement
        # into a hidden second setup threshold.
        _publish_price_gate(
            "ACCEPTED", proposed=executable,
            displacement_percent_value=_actual_displacement(executable),
            displacement_dollars=executable - structural, effective_maximum=maximum,
        )
        return replace(
            signal,
            entry_trigger=executable,
            reference_price=executable,
            risk_per_share=risk,
            # Paying more does not create a higher structural target.
            target_levels=signal.target_levels,
            structural_entry_trigger=structural,
        )

    def _record_entry_extension(
        self,
        value: PointInTimeObservation,
        candidate: MomentumCandidate,
        signal: MomentumEntrySignal,
        *,
        entry_price: Decimal,
        displacement_limit: Decimal,
    ) -> None:
        """Publish bounded extension evidence without affecting authority."""
        try:
            lifecycle = self._setup_lifecycles.get(candidate.symbol.strip().upper(), {})
            bars = tuple(value.bars[-20:])
            vwap = None
            volume = sum((bar.volume for bar in bars), ZERO)
            if volume > ZERO:
                vwap = sum((bar.close * bar.volume for bar in bars), ZERO) / volume
            hod = None
            if candidate.distance_from_hod_percent is not None and candidate.price > ZERO:
                hod = candidate.price / (
                    Decimal("1") - candidate.distance_from_hod_percent / HUNDRED
                )
            assessment = assess_entry_extension(
                entry_price=entry_price,
                trigger_price=signal.structural_entry_trigger or signal.entry_trigger,
                structural_stop=signal.stop_price,
                first_observation_price=lifecycle.get("price_at_first_observation"),
                forming_price=lifecycle.get("price_at_first_forming"),
                hod=hod, vwap=vwap,
                trigger_at=lifecycle.get("first_setup_triggered_at"),
                evaluated_at=value.evaluation_timestamp or value.observation.timestamp,
                displacement_limit=displacement_limit,
            )
            signature = (assessment.classification, assessment.trigger_extension_percent,
                         assessment.risk_normalized_extension)
            prior = lifecycle.get("entry_extension_signature")
            lifecycle["entry_extension_signature"] = signature
            lifecycle["entry_extension"] = assessment
            if prior == signature or self.writer is None:
                return
            timestamp = value.evaluation_timestamp or value.observation.timestamp
            payload = {
                "classification": assessment.classification,
                "trigger_price": signal.structural_entry_trigger or signal.entry_trigger,
                "structural_stop": signal.stop_price,
                "entry_price": entry_price,
                "trigger_extension_percent": assessment.trigger_extension_percent,
                "risk_normalized_extension": assessment.risk_normalized_extension,
                "hod_distance_percent": assessment.hod_distance_percent,
                "at_hod": assessment.at_hod,
                "vwap_extension_percent": assessment.vwap_extension_percent,
                "move_since_first_observation_percent": assessment.move_since_observation_percent,
                "move_since_forming_percent": assessment.move_since_forming_percent,
                "move_since_trigger_percent": assessment.move_since_trigger_percent,
                "seconds_since_trigger": assessment.seconds_since_trigger,
                "setup_type": signal.setup_type.value,
            }
            self.writer.submit_diagnostic(CaptureRecord.create(
                CaptureRecordType.ENTRY_EXTENSION, candidate.symbol, timestamp,
                payload, identity_parts=(assessment.classification, str(entry_price)),
            ))
        except Exception:
            # Extension evidence is diagnostic only and cannot affect entry.
            return

    def _consider_adaptive_entry_replacement(
        self,
        value: PointInTimeObservation,
        candidate: MomentumCandidate,
        signal: MomentumEntrySignal,
        account: PaperAccountContext,
        *,
        thesis_valid: bool = True,
        signal_source: str = "CURRENT_ENTRY_SIGNAL",
    ) -> None:
        """Use one fresh observation to consider a bounded entry replacement."""
        state = self._paper.get(signal.symbol)
        ask = value.observation.ask
        bid = value.observation.bid
        def record_pursuit(reason: str) -> None:
            performance_diagnostics.record_pursuit_evaluation(
                reason=reason,
                phase="RUNTIME_GATE",
                timestamp=value.evaluation_timestamp or value.observation.timestamp,
                symbol=signal.symbol,
                lifecycle_id=lifecycle_identity(signal),
                current_limit=signal.entry_trigger,
                bid=bid,
                ask=ask,
                last=value.observation.price,
                quote_age=value.quote_freshness_seconds,
                signal_age=value.last_price_freshness_seconds,
                spread=candidate.spread_percent,
                original_entry=signal.entry_trigger,
                structural_stop=(None if state is None else state.signal.stop_price),
                replacement_count=0,
                max_replacements=self.config.adaptive_entry.max_replacements,
                signal_source=signal_source,
                thesis_valid=thesis_valid,
            )
        if state is not None and state.remaining < state.initial_quantity:
            performance_diagnostics.record_entry_counter(
                "post_partial_pursuit_evaluations"
            )
        if state is None or ask is None or bid is None:
            record_pursuit("MISSING_BID_ASK")
            return

        if not thesis_valid:
            record_pursuit("THESIS_INVALID")
            return

        stale_limit = self.capture_config.quote_stale_after_seconds
        freshness = (
            value.quote_freshness_seconds,
            value.last_price_freshness_seconds,
            value.processing_age_seconds,
            None if value.retained_reevaluation else value.delivery_age_seconds,
        )
        if (
            not value.halt_state_known
            or not value.volume_known
            or not value.observation.tradable
            or value.observation.halted
            or signal.session not in self.config.entry.allowed_sessions
            or any(age is None or age > stale_limit for age in freshness)
        ):
            record_pursuit("QUOTE_STALE")
            return

        # This gate also applies to the legacy path without depth/size data.
        if not remaining_reward_ok(
            entry=Decimal(ask), stop=state.signal.stop_price,
            targets=state.signal.target_levels, spread=Decimal(ask) - Decimal(bid),
            minimum_first_r=self.config.entry.minimum_remaining_first_target_r,
            minimum_final_r=self.config.entry.minimum_remaining_final_target_r,
        ):
            record_pursuit("INSUFFICIENT_REMAINING_REWARD")
            return
        spread = candidate.spread_percent
        if (
            value.best_bid_size is not None
            and value.best_ask_size is not None
            and spread is not None
        ):
            replacement_budget = True
            quality_ok = True
            from app.trade_intelligence.opportunity_memory import OpportunityQuality
            opportunity_id = self._memory_opportunity_ids.get(signal.symbol)
            if opportunity_id is not None:
                memory = self.opportunity_memory.get(
                    signal.timestamp.date(), signal.symbol, opportunity_id,
                )
                quality_ok = memory is None or memory.quality_classification not in {
                    # Quality is an explicit blocker when already assessed as
                    # exhausted/unavailable; absent quality remains compatible
                    # with the pre-existing adaptive-entry path.
                    OpportunityQuality.EXHAUSTED,
                    OpportunityQuality.UNAVAILABLE,
                }
            depth = depth_features(value.depth_bids, value.depth_asks)
            prior_depth = self._last_depth_by_symbol.get(signal.symbol)
            ask_state, bid_advancing = depth_transition(
                None if prior_depth is None else prior_depth[0],
                None if prior_depth is None else prior_depth[1],
                value.depth_bids, value.depth_asks,
            )
            if value.depth_bids and value.depth_asks:
                self._last_depth_by_symbol[signal.symbol] = (
                    value.depth_bids, value.depth_asks,
                )
            assessment = assess_top_of_book_pursuit(
                evaluated_at=value.evaluation_timestamp or value.observation.timestamp,
                working_limit=signal.entry_trigger,
                best_bid=bid, best_ask=ask,
                bid_size=value.best_bid_size, ask_size=value.best_ask_size,
                spread_percent=spread,
                maximum_spread_percent=self.runtime.execution_spread_limit(candidate),
                quote_fresh=all(
                    age is not None and age >= ZERO and age <= stale_limit
                    for age in freshness
                ),
                liquidity_ok=self.runtime.current_execution_liquidity_ok(
                    candidate,
                    quote_fresh=all(
                        age is not None and age >= ZERO and age <= stale_limit
                        for age in freshness
                    ),
                ),
                thesis_valid=thesis_valid, quality_ok=quality_ok,
                replacement_budget_available=replacement_budget,
                structural_stop=state.signal.stop_price,
                expected_reward=(
                    None if not state.signal.target_levels
                    else state.signal.target_levels[-1] - Decimal(ask)
                ),
                depth=depth, ask_state=ask_state,
                bid_advancing=bid_advancing,
                flow=value.order_flow,
            )
            self._last_execution_pursuit[signal.symbol] = assessment
            record_pursuit(assessment.reason or assessment.decision.value)
            if assessment.decision is not ExecutionPursuitDecision.PURSUE_ONE_LEVEL:
                return

        def current_gates_valid() -> bool:
            return bool(
                value.halt_state_known
                and value.volume_known
                and value.observation.tradable
                and not value.observation.halted
                and signal.session in self.config.entry.allowed_sessions
                and self.runtime.current_execution_liquidity_ok(
                    candidate,
                    quote_fresh=all(
                        age is not None and age >= ZERO and age <= stale_limit
                        for age in freshness
                    ),
                )
                and account.risk_engine_approved
                and Decimal(ask) > state.signal.stop_price > ZERO
                and (Decimal(ask) - state.signal.stop_price) / Decimal(ask) * Decimal("100")
                    <= self.config.risk.maximum_stop_distance_percent
                and not account.broker_restriction
                and thesis_valid
                and self._execution_permitted()
            )

        try:
            self._paper_entry_replacer(
                lifecycle_id=lifecycle_identity(signal),
                current_ask=Decimal(ask),
                now=value.evaluation_timestamp or value.observation.timestamp,
                structural_stop=state.signal.stop_price,
                original_risk_budget=state.risk_budget,
                account_equity=account.equity,
                buying_power=account.buying_power,
                existing_exposure=account.existing_exposure,
                maximum_position_equity_percentage=self.config.risk.maximum_position_equity_percentage,
                maximum_position_dollars=self.config.risk.maximum_position_dollars,
                maximum_quantity=self.config.risk.maximum_quantity,
                revalidate=current_gates_valid,
                policy=PaperEntryReplacementPolicy(
                    max_replacements=self.config.adaptive_entry.max_replacements,
                    min_reprice_interval_seconds=self.config.adaptive_entry.min_reprice_interval_seconds,
                    max_displacement_percent=self.runtime.execution_displacement_percent(candidate),
                    max_displacement_absolute=Decimal("0.50"),
                ),
            )
        except Exception:
            # Replacement is optional execution enhancement.  Its failure must
            # never change the primary observation or entry decision.
            return

    def _consider_fast_momentum_rearm(
        self,
        value: PointInTimeObservation,
        candidate: MomentumCandidate,
        signal: MomentumEntrySignal,
        account: PaperAccountContext,
    ) -> None:
        """Re-arm only after a terminal unfilled lifecycle, never a position."""
        if self._paper_entry_rearmer is None:
            return
        stale_limit = self.capture_config.quote_stale_after_seconds
        quote_fresh = all(
            age is not None and ZERO <= age <= stale_limit
            for age in (
                value.quote_freshness_seconds,
                value.last_price_freshness_seconds,
            )
        ) and all(
            age is None or ZERO <= age <= stale_limit
            for age in (
                value.processing_age_seconds,
                None if value.retained_reevaluation else value.delivery_age_seconds,
            )
        )
        if not self.runtime.current_execution_liquidity_ok(
            candidate, quote_fresh=quote_fresh,
        ):
            return
        position = size_position(
            signal, account_equity=account.equity,
            buying_power=account.buying_power,
            allowed_symbols=account.allowed_symbols,
            existing_exposure=account.existing_exposure,
            exposure_limit=account.exposure_limit,
            risk_engine_approved=account.risk_engine_approved,
            broker_restriction=account.broker_restriction,
            config=self.config.risk,
            symbol_authorized=_paper_symbol_authorization(signal, account).authorized,
        )
        if not position.approved:
            return
        try:
            result = self._paper_entry_rearmer(
                signal, position.shares, position.risk_dollars,
                opportunity_id=opportunity_identity(signal),
                opportunity_anchor=self._paper[signal.symbol].signal.entry_trigger,
                max_lifecycles_per_opportunity=self.config.adaptive_entry.max_lifecycles_per_opportunity,
                max_displacement_percent=self.runtime.execution_displacement_percent(candidate),
                max_displacement_absolute=Decimal("0.50"),
                now=value.evaluation_timestamp or value.observation.timestamp,
            )
            authorized = (
                result.authorized
                if isinstance(result, PaperEntryAuthorizationDecision)
                else bool(result)
            )
            if authorized:
                self._install_rearmed_paper_state(
                    signal, position.shares, position.risk_dollars,
                    value.evaluation_timestamp or value.observation.timestamp,
                )
        except Exception:
            return

    def authorize_new_structural_entry(
        self, candidate: MomentumCandidate, signal: MomentumEntrySignal,
        account: PaperAccountContext, *, higher_low: bool = False,
        reclaim: bool = False, momentum_reaccelerated: bool = False,
        working_entry: bool = False, freshness_ok: bool,
    ) -> bool:
        """Authorize a fresh structural thesis, separate from old-entry reprice.

        The caller supplies a newly assessed signal.  This seam never changes
        the ordinary observation path; it only permits an explicit new
        lifecycle after memory and the existing risk gates approve it.
        """
        from app.trade_intelligence.opportunity_memory import OpportunityTransitionType
        opportunity_id = self._memory_opportunity_ids.get(signal.symbol) or opportunity_identity(signal)
        memory_record = self.opportunity_memory.get(
            signal.timestamp.date(), signal.symbol, opportunity_id,
        )
        lifecycle_count = 0 if memory_record is None else len(memory_record.attempts)
        assessment = self.opportunity_memory.assess_new_structure(
            signal.timestamp.date(), signal.symbol, opportunity_id,
            entry_anchor=signal.entry_trigger, structural_stop=signal.stop_price,
            spread_ok=self.runtime.current_execution_liquidity_ok(
                candidate, quote_fresh=freshness_ok,
            ),
            liquidity_ok=self.runtime.current_execution_liquidity_ok(
                candidate, quote_fresh=freshness_ok,
            ),
            freshness_ok=freshness_ok,
            position_quantity=(self._paper_position_quantity_source(signal.symbol)
                              if self._paper_position_quantity_source is not None else ZERO),
            working_entry=working_entry,
            lifecycle_count=lifecycle_count,
            max_lifecycles=self.config.adaptive_entry.max_lifecycles_per_opportunity,
            higher_low=higher_low, reclaim=reclaim,
            momentum_reaccelerated=momentum_reaccelerated,
        )
        if not assessment.eligible:
            self.opportunity_memory.record_transition(
                signal.timestamp.date(), signal.symbol, opportunity_id,
                signal.timestamp, OpportunityTransitionType.ENTRY_CANCELLED,
                reason=assessment.reason,
            )
            return False
        quality = self.opportunity_memory.assess_quality(
            signal.timestamp.date(), signal.symbol, opportunity_id,
            proposed_entry=signal.entry_trigger,
            structural_stop=signal.stop_price,
            evaluated_at=signal.timestamp,
            session=signal.session,
            reward_reference=(None if memory_record is None else memory_record.highest_price_since_start),
            lifecycle_count=lifecycle_count,
        )
        if not quality.authorization_allowed:
            self.opportunity_memory.record_transition(
                signal.timestamp.date(), signal.symbol, opportunity_id,
                signal.timestamp, OpportunityTransitionType.ENTRY_CANCELLED,
                reason=f"QUALITY_{quality.classification.value}:{','.join(quality.reasons)}",
            )
            return False
        position = size_position(
            signal, account_equity=account.equity, buying_power=account.buying_power,
            allowed_symbols=account.allowed_symbols, existing_exposure=account.existing_exposure,
            exposure_limit=account.exposure_limit, risk_engine_approved=account.risk_engine_approved,
            broker_restriction=account.broker_restriction, config=self.config.risk,
            symbol_authorized=_paper_symbol_authorization(signal, account).authorized,
        )
        if (
            not position.approved or self._paper_entry_submitter is None
            or candidate.halted or not candidate.tradable
            or signal.session not in self.config.entry.allowed_sessions
        ):
            return False
        result = self._paper_entry_submitter(signal, position.shares, position.risk_dollars)
        authorized = result.authorized if isinstance(result, PaperEntryAuthorizationDecision) else bool(result)
        if authorized:
            self.opportunity_memory.record_transition(
                signal.timestamp.date(), signal.symbol, opportunity_id,
                signal.timestamp, OpportunityTransitionType.ENTRY_ATTEMPT,
                reason="NEW_STRUCTURAL_ENTRY", price=signal.entry_trigger,
                quantity=Decimal(position.shares),
            )
        return authorized

    def authorize_live_structural_entry(
        self, candidate: MomentumCandidate, signal: MomentumEntrySignal,
        account: PaperAccountContext, *, structure_established_at: datetime,
        live_price: Decimal, trigger_price: Decimal,
        higher_low: bool = False, reclaim: bool = False,
        momentum_reaccelerated: bool = False, working_entry: bool = False,
        freshness_ok: bool,
    ) -> bool:
        """Authorize the final trigger from a fresh quote/trade observation.

        Completed bars (or an equivalent trusted detector) must establish the
        structure first.  This method only removes the needless wait for a
        subsequent bar close; it does not bypass any execution gate.
        """
        from app.trade_intelligence.opportunity_memory import (
            EntryAttemptSummary, OpportunityTransitionType,
        )
        opportunity_id = self._memory_opportunity_ids.get(signal.symbol) or opportunity_identity(signal)
        trading_date = signal.timestamp.date()
        record = self.opportunity_memory.get(trading_date, signal.symbol, opportunity_id)
        if record is None:
            return False
        self.opportunity_memory.record_structure_established(
            trading_date, signal.symbol, opportunity_id, structure_established_at,
        )
        position_quantity = (
            self._paper_position_quantity_source(signal.symbol)
            if self._paper_position_quantity_source is not None else ZERO
        )
        assessment = self.opportunity_memory.assess_live_trigger(
            trading_date, signal.symbol, opportunity_id,
            entry_anchor=signal.entry_trigger, structural_stop=signal.stop_price,
            trigger_price=trigger_price, live_price=live_price,
            spread_ok=self.runtime.current_execution_liquidity_ok(
                candidate, quote_fresh=freshness_ok,
            ),
            liquidity_ok=self.runtime.current_execution_liquidity_ok(
                candidate, quote_fresh=freshness_ok,
            ),
            freshness_ok=freshness_ok, position_quantity=position_quantity,
            working_entry=working_entry,
            lifecycle_count=len(record.attempts),
            max_lifecycles=self.config.adaptive_entry.max_lifecycles_per_opportunity,
            higher_low=higher_low, reclaim=reclaim,
            momentum_reaccelerated=momentum_reaccelerated,
        )
        if not assessment.eligible:
            self.opportunity_memory.record_transition(
                trading_date, signal.symbol, opportunity_id, signal.timestamp,
                OpportunityTransitionType.ENTRY_CANCELLED, reason=assessment.reason,
            )
            return False
        if candidate.halted or not candidate.tradable or signal.session not in self.config.entry.allowed_sessions:
            return False
        position = size_position(
            signal, account_equity=account.equity, buying_power=account.buying_power,
            allowed_symbols=account.allowed_symbols, existing_exposure=account.existing_exposure,
            exposure_limit=account.exposure_limit, risk_engine_approved=account.risk_engine_approved,
            broker_restriction=account.broker_restriction, config=self.config.risk,
            symbol_authorized=_paper_symbol_authorization(signal, account).authorized,
        )
        if not position.approved or self._paper_entry_submitter is None:
            return False
        if (
            record.live_trigger_first_seen_at is not None
            and record.live_trigger_anchor == signal.entry_trigger
        ):
            return False
        result = self._paper_entry_submitter(signal, position.shares, position.risk_dollars)
        authorized = result.authorized if isinstance(result, PaperEntryAuthorizationDecision) else bool(result)
        if authorized:
            first_trigger = self.opportunity_memory.record_live_trigger(
                trading_date, signal.symbol, opportunity_id, signal.timestamp,
                price=live_price, anchor=signal.entry_trigger,
            )
            if not first_trigger:
                return False
            self.opportunity_memory.record_entry_attempt(
                trading_date, signal.symbol, opportunity_id,
                EntryAttemptSummary(
                    lifecycle_id=lifecycle_identity(signal), setup=signal.setup_type,
                    attempted_at=signal.timestamp, requested_price=signal.entry_trigger,
                    requested_quantity=Decimal(position.shares), structural_stop=signal.stop_price,
                    spread=candidate.spread_percent, liquidity=candidate.dollar_volume,
                    risk=signal.risk_per_share, result="NEW_STRUCTURAL_ENTRY",
                    reason="LIVE_TRIGGER",
                ),
                structure_established_at=structure_established_at,
                live_trigger_first_seen_at=signal.timestamp,
                entry_authorized_at=signal.timestamp,
                order_submitted_at=signal.timestamp,
            )
            self.opportunity_memory.record_transition(
                trading_date, signal.symbol, opportunity_id, signal.timestamp,
                OpportunityTransitionType.NEW_STRUCTURE_ENTRY_READY,
                reason="LIVE_TRIGGER", price=signal.entry_trigger,
                quantity=Decimal(position.shares),
            )
        return authorized

    def authorize_recovered_continuation(
        self, candidate: MomentumCandidate, signal: MomentumEntrySignal,
        account: PaperAccountContext, *, structure: str,
        classification: str, structure_established_at: datetime,
        live_price: Decimal, trigger_price: Decimal,
        reclaim_level: Decimal | None = None,
        higher_low: bool = False, reclaim: bool = False,
        momentum_reaccelerated: bool = False, working_entry: bool = False,
        freshness_ok: bool,
    ) -> bool:
        """Authorize a new continuation lifecycle from an existing thesis.

        This is an explicit Phase 4 seam.  The ordinary ``observe`` path is
        unchanged; callers must provide detector-established structure and a
        fresh final trigger.  Re-evaluation after spread/liquidity recovery
        therefore uses the same opportunity memory, but never the old entry
        anchor or adaptive replacement path.
        """
        from app.trade_intelligence.opportunity_memory import (
            OpportunityTransitionType, PullbackClassification,
        )

        try:
            pullback_classification = PullbackClassification(classification)
        except ValueError:
            return False
        opportunity_id = (
            self._memory_opportunity_ids.get(signal.symbol)
            or opportunity_identity(signal)
        )
        trading_date = signal.timestamp.date()
        record = self.opportunity_memory.get(
            trading_date, signal.symbol, opportunity_id,
        )
        if record is None:
            return False
        assessment = self.opportunity_memory.assess_continuation_structure(
            trading_date, signal.symbol, opportunity_id,
            entry_anchor=signal.entry_trigger,
            structural_stop=signal.stop_price,
            spread_ok=self.runtime.current_execution_liquidity_ok(
                candidate, quote_fresh=freshness_ok,
            ),
            liquidity_ok=self.runtime.current_execution_liquidity_ok(
                candidate, quote_fresh=freshness_ok,
            ),
            freshness_ok=freshness_ok,
            classification=pullback_classification,
            structure=structure,
            structure_established=True,
            position_quantity=(self._paper_position_quantity_source(signal.symbol)
                               if self._paper_position_quantity_source is not None else ZERO),
            working_entry=working_entry,
            lifecycle_count=len(record.attempts),
            max_lifecycles=self.config.adaptive_entry.max_lifecycles_per_opportunity,
            reclaim_level=reclaim_level,
        )
        if not assessment.eligible:
            self.opportunity_memory.record_transition(
                trading_date, signal.symbol, opportunity_id, signal.timestamp,
                OpportunityTransitionType.ENTRY_CANCELLED,
                reason=assessment.reason,
            )
            return False
        quality = self.opportunity_memory.assess_quality(
            trading_date, signal.symbol, opportunity_id,
            proposed_entry=signal.entry_trigger,
            structural_stop=signal.stop_price,
            evaluated_at=signal.timestamp,
            session=signal.session,
            reward_reference=record.highest_price_since_start,
            lifecycle_count=len(record.attempts),
        )
        if not quality.authorization_allowed:
            self.opportunity_memory.record_transition(
                trading_date, signal.symbol, opportunity_id,
                signal.timestamp, OpportunityTransitionType.ENTRY_CANCELLED,
                reason=f"QUALITY_{quality.classification.value}:{','.join(quality.reasons)}",
            )
            return False
        if reclaim_level is not None:
            self.opportunity_memory.record_reclaim(
                trading_date, signal.symbol, opportunity_id,
                signal.timestamp, reclaim_level, confirmed=True,
            )
        return self.authorize_live_structural_entry(
            candidate, signal, account,
            structure_established_at=structure_established_at,
            live_price=live_price, trigger_price=trigger_price,
            higher_low=higher_low, reclaim=reclaim,
            momentum_reaccelerated=momentum_reaccelerated,
            working_entry=working_entry, freshness_ok=freshness_ok,
        )

    def consider_add_on(
        self,
        candidate: MomentumCandidate,
        signal: MomentumEntrySignal,
        account: PaperAccountContext,
        *,
        value: PointInTimeObservation | None = None,
        continuation_valid: bool = True,
    ) -> bool:
        """Authorize at most one separately-proven add-on leg for a parent.

        This is deliberately explicit; ordinary repeated entry observations do not
        become add-ons.  The caller must supply a newly assessed Warrior setup.
        """
        state = self._paper.get(signal.symbol)
        setup = candidate.setup
        if value is None:
            return False
        if (
            state is None or state.add_on_used or state.add_on is not None
            or self._paper_position_quantity_source is None
            or self._paper_position_quantity_source(signal.symbol) <= 0
            or not state.first_taken
            or not continuation_valid
            or setup is None or setup.state is not SetupState.TRIGGERED
            or setup.trigger is None or setup.stop_price is None
            or candidate.status is not CandidateStatus.ENTRY_READY
            or candidate.price <= state.entry_price
            or not self.runtime.current_execution_liquidity_ok(
                candidate,
                quote_fresh=(
                    value.halt_state_known and value.volume_known
                    and value.quote_freshness_seconds is not None
                    and value.last_price_freshness_seconds is not None
                    and ZERO <= value.quote_freshness_seconds <= self.capture_config.quote_stale_after_seconds
                    and ZERO <= value.last_price_freshness_seconds <= self.capture_config.quote_stale_after_seconds
                ),
            )
            or not candidate.tradable or candidate.halted
            or signal.session not in self.config.entry.allowed_sessions
            or not account.risk_engine_approved or account.broker_restriction
        ):
            return False
        if value is not None:
            stale = self.capture_config.quote_stale_after_seconds
            if (
                not value.halt_state_known or not value.volume_known
                or value.quote_freshness_seconds is None
                or value.last_price_freshness_seconds is None
                or value.quote_freshness_seconds > stale
                or value.last_price_freshness_seconds > stale
                or value.observation.bid is None or value.observation.ask is None
            ):
                return False
        risk_per_share = signal.risk_per_share
        if risk_per_share <= ZERO:
            return False
        parent_risk = state.signal.risk_per_share
        remaining_risk = max(
            ZERO, state.risk_budget - Decimal(max(0, state.remaining)) * parent_risk,
        )
        risk_quantity = int((remaining_risk / risk_per_share).to_integral_value(rounding=ROUND_FLOOR))
        price = signal.entry_trigger
        notional_room = max(
            ZERO,
            min(
                self.config.risk.maximum_position_dollars,
                account.equity * self.config.risk.maximum_position_equity_percentage,
            ) - account.existing_exposure,
        )
        notional_quantity = int((notional_room / price).to_integral_value(rounding=ROUND_FLOOR))
        buying_power_quantity = int((account.buying_power / price).to_integral_value(rounding=ROUND_FLOOR))
        quantity = max(0, min(
            risk_quantity, notional_quantity, buying_power_quantity,
            self.config.risk.maximum_quantity,
        ))
        if quantity <= 0:
            return False
        add_on_id = f"{lifecycle_identity(state.signal)}:ADD_ON_1"
        if self._paper_add_on_submitter is not None:
            try:
                result = self._paper_add_on_submitter(
                    signal, quantity, risk_per_share * quantity,
                    parent_lifecycle_id=lifecycle_identity(state.signal),
                    add_on_id=add_on_id,
                )
            except Exception:
                return False
            accepted = result.authorized if isinstance(result, PaperEntryAuthorizationDecision) else bool(result)
            if not accepted:
                return False
        leg = _AddOnLeg(
            add_on_id, lifecycle_identity(state.signal), signal, quantity,
            filled_quantity=quantity if self._paper_add_on_submitter is None else 0,
            remaining=quantity if self._paper_add_on_submitter is None else 0,
            stop=signal.stop_price,
            peak_price=signal.entry_trigger if self._paper_add_on_submitter is None else None,
            peak_r=ZERO if self._paper_add_on_submitter is None else None,
            current_r=ZERO if self._paper_add_on_submitter is None else None,
            risk_consumed=(risk_per_share * quantity if self._paper_add_on_submitter is None else ZERO),
        )
        state.add_on = leg
        state.add_on_used = True
        self._submit_records((_management_context_record(
            signal.symbol, signal.timestamp, state.signal, state,
        ),))
        return True

    def reconcile_add_on_fill(
        self, symbol: str, quantity: int, fill_price: Decimal,
    ) -> bool:
        """Apply an authoritative add-on fill without changing parent milestones."""
        state = self._paper.get(symbol.strip().upper())
        if state is None or state.add_on is None or quantity <= 0:
            return False
        leg = state.add_on
        remaining_requested = max(0, leg.requested_quantity - leg.filled_quantity)
        filled = min(quantity, remaining_requested)
        if filled <= 0:
            return False
        leg.filled_quantity += filled
        leg.remaining += filled
        leg.risk_consumed = leg.filled_quantity * max(
            ZERO, fill_price - (leg.stop or fill_price),
        )
        if self._paper_position_quantity_source is not None:
            authoritative = max(0, int(self._paper_position_quantity_source(symbol)))
            if authoritative > 0:
                state.remaining = authoritative
                if self._paper_exit_submitter is not None:
                    try:
                        self._paper_exit_submitter(
                            symbol, authoritative, state.stop,
                            "STOP", leg.parent_lifecycle_id,
                        )
                    except Exception:
                        # The position remains authoritative; subsequent
                        # management will fail closed if protection is absent.
                        pass
        leg.peak_price = fill_price if leg.peak_price is None else max(leg.peak_price, fill_price)
        if leg.signal.risk_per_share > ZERO:
            leg.peak_r = (leg.peak_price - leg.signal.entry_trigger) / leg.signal.risk_per_share
            leg.current_r = leg.peak_r
        self._submit_records((_management_context_record(
            symbol, state.signal.timestamp, state.signal, state,
        ),))
        return True

    def exit_add_on(self, symbol: str, price: Decimal) -> bool:
        """Request a bounded LIMIT exit for only the add-on leg."""
        state = self._paper.get(symbol.strip().upper())
        if state is None or state.add_on is None or not state.add_on.active:
            return False
        leg = state.add_on
        if leg.remaining <= 0 or price <= 0:
            return False
        quantity = leg.remaining
        if self._paper_position_quantity_source is not None:
            quantity = min(quantity, max(0, int(self._paper_position_quantity_source(symbol))))
        if quantity <= 0:
            return False
        if self._paper_exit_submitter is not None:
            try:
                result = self._paper_exit_submitter(
                    symbol, quantity, price, "AUTONOMOUS_ADD_ON_EXIT",
                    leg.parent_lifecycle_id,
                )
            except Exception:
                return False
            accepted = result.state is PaperExitSubmissionState.SUBMITTED if isinstance(result, PaperExitSubmissionDecision) else bool(result)
            if not accepted:
                return False
        leg.remaining -= quantity
        if leg.remaining <= 0:
            leg.remaining = 0
            leg.active = False
        self._submit_records((_management_context_record(
            symbol, state.signal.timestamp, state.signal, state,
        ),))
        return True

    def observe_intraminute_shadow(
        self, market: ShadowMarketObservation,
    ) -> None:
        """Append research records without invoking strategy or execution."""

        if self._latched_shadow is None:
            return
        records = self._latched_shadow.observe(market)
        if records:
            self._submit_records(records)

    def invalidate_intraminute_shadow(
        self, symbol: str, timestamp: datetime,
        transition: ShadowLatchedTransition,
        *, reason: str, processing_time: datetime | None = None,
    ) -> None:
        if self._latched_shadow is None:
            return
        records = self._latched_shadow.invalidate(
            symbol, timestamp, transition, reason=reason,
            processing_time=processing_time,
        )
        if records:
            self._submit_records(records)

    def shutdown_intraminute_shadow(self, timestamp: datetime) -> None:
        if self._latched_shadow is None:
            return
        records = self._latched_shadow.shutdown(timestamp)
        if records:
            self._submit_records(records)

    def _completed_bar_snapshot(self, value: object) -> _CompletedBarSnapshot:
        observation = value.observation
        symbol = str(observation.symbol).strip().upper()
        session = str(value.session).upper()
        cutoff_minute = observation.timestamp.replace(second=0, microsecond=0)
        source_bars = (
            value.bars if isinstance(value.bars, tuple) else tuple(value.bars)
        )
        cached = self._completed_bar_cache.get(symbol)
        same_source = (
            cached is not None
            and cached.session == session
            and cached.source_bars == source_bars
        )
        if same_source and (
            cached.cutoff_minute == cutoff_minute
            or cached.completed == source_bars
        ):
            snapshot = cached
            if cached.cutoff_minute != cutoff_minute:
                snapshot = _CompletedBarSnapshot(
                    source_bars=source_bars,
                    session=session,
                    cutoff_minute=cutoff_minute,
                    revision=cached.revision,
                    completed=cached.completed,
                )
                self._completed_bar_cache[symbol] = snapshot
            self._completed_bar_cache.move_to_end(symbol)
            self._completed_bar_cache_hits += 1
            self._completed_bar_duplicate_bars_suppressed += len(snapshot.completed)
            self._record_completed_bar_metric("completed_bar.cache_hit")
            if snapshot.completed:
                self._record_completed_bar_metric(
                    "completed_bar.duplicate_bars_suppressed",
                    count=len(snapshot.completed),
                )
            return snapshot

        completed = canonical_completed_history(
            source_bars, observation.timestamp, session=value.session,
        )
        revision = 1
        if cached is not None and cached.session == session:
            revision = cached.revision + (completed != cached.completed)
        snapshot = _CompletedBarSnapshot(
            source_bars=source_bars,
            session=session,
            cutoff_minute=cutoff_minute,
            revision=revision,
            completed=completed,
        )
        self._completed_bar_cache[symbol] = snapshot
        self._completed_bar_cache.move_to_end(symbol)
        while len(self._completed_bar_cache) > 512:
            self._completed_bar_cache.popitem(last=False)
        self._completed_bar_cache_misses += 1
        self._record_completed_bar_metric("completed_bar.cache_miss")
        return snapshot

    def _deliver_completed_bar_deltas(
        self,
        symbol: str,
        snapshot: _CompletedBarSnapshot,
        observed_at: datetime,
    ) -> None:
        completed = snapshot.completed

        capture_revision = (snapshot.session, snapshot.revision)
        if self._completed_bar_capture_revision.get(symbol) != capture_revision:
            capture_delta = tuple(
                bar for bar in completed
                if (symbol, bar.timestamp) not in self._seen_bars
            )
            if capture_delta:
                self._submit_records(tuple(
                    _bar_record(bar, observed_at) for bar in capture_delta
                ))
                for bar in capture_delta:
                    self._seen_bars.add((symbol, bar.timestamp))
                self._completed_bar_capture_cursor[symbol] = (
                    capture_delta[-1].timestamp
                )
                self._completed_bar_new_bars_processed += len(capture_delta)
                self._record_completed_bar_metric(
                    "completed_bar.new_bars_processed",
                    count=len(capture_delta),
                )
            self._completed_bar_capture_revision[symbol] = capture_revision

        state = self._paper.get(symbol)
        if (
            state is not None
            and completed
            and (
                state.last_bar_timestamp is None
                or completed[-1].timestamp > state.last_bar_timestamp
            )
        ):
            lifecycle_delta = tuple(
                bar for bar in completed
                if bar.timestamp >= state.signal.timestamp
                and (
                    state.last_bar_timestamp is None
                    or bar.timestamp > state.last_bar_timestamp
                )
            )
            if lifecycle_delta:
                self._completed_bar_lifecycle_catchup_bars += len(lifecycle_delta)
                self._record_completed_bar_metric(
                    "completed_bar.lifecycle_catchup_bars",
                    count=len(lifecycle_delta),
                )
            for bar in lifecycle_delta:
                current = self._paper.get(symbol)
                if current is not state:
                    break
                records = self._advance_paper(state, bar, observed_at)
                if records:
                    self._submit_records(records)

        counter = self._counterfactual.get(symbol)
        if (
            counter is not None
            and completed
            and (
                counter.last_bar_timestamp is None
                or completed[-1].timestamp > counter.last_bar_timestamp
            )
        ):
            counter_delta = tuple(
                bar for bar in completed
                if (
                    counter.last_bar_timestamp is None
                    or bar.timestamp > counter.last_bar_timestamp
                )
            )
            for bar in counter_delta:
                current = self._counterfactual.get(symbol)
                if current is not counter:
                    break
                records = self._advance_counterfactual(counter, bar, observed_at)
                if records:
                    self._submit_records(records)

        handoff = self._completed_bar_research_handoff
        if handoff is None or self._shadow is None:
            return
        research_cursor = self._completed_bar_research_cursor.get(symbol)
        if (
            not completed
            or (
                research_cursor is not None
                and completed[-1].timestamp <= research_cursor
            )
        ):
            return
        research_delta = tuple(
            bar for bar in completed
            if research_cursor is None or bar.timestamp > research_cursor
        )
        for bar in research_delta:
            evaluation_ids = self._shadow.active_evaluation_ids(symbol)
            if not evaluation_ids:
                self._completed_bar_research_cursor[symbol] = bar.timestamp
                continue
            handoff.start()
            work = CompletedBarResearchWork(
                symbol=symbol,
                session=snapshot.session,
                revision=snapshot.revision,
                bar=bar,
                shadow_evaluation_ids=evaluation_ids,
            )
            started = perf_counter()
            result = handoff.submit(work)
            self._record_completed_bar_duration(
                "completed_bar.research_submit",
                started,
                symbol=symbol,
                success=result is not CompletedBarSubmitResult.REJECTED,
            )
            if result is CompletedBarSubmitResult.REJECTED:
                break
            self._completed_bar_research_cursor[symbol] = bar.timestamp
            if result is CompletedBarSubmitResult.ACCEPTED:
                self._completed_bar_research_submits += 1
            else:
                self._completed_bar_research_duplicate_suppressed += 1
                self._record_completed_bar_metric(
                    "completed_bar.research_duplicate_suppressed",
                )

    def _process_completed_bar_research(
        self, work: CompletedBarResearchWork,
    ) -> None:
        started = perf_counter()
        success = False
        try:
            if self._shadow is None:
                return
            records = self._shadow.observe_bar_for_evaluations(
                work.bar, work.shadow_evaluation_ids,
            )
            if records:
                self._submit_records(records)
            success = True
        finally:
            self._record_completed_bar_duration(
                "completed_bar.research_worker",
                started,
                symbol=work.symbol,
                success=success,
            )

    def _paper_order_book(self) -> object | None:
        submitter = self._paper_exit_submitter
        owner = getattr(submitter, "__self__", None)
        return getattr(owner, "order_book", None)

    def _durable_exit_leg(
        self,
        state: _PaperState,
        role: str,
        *,
        desired_quantity: int | None = None,
        desired_price: Decimal | None = None,
    ) -> _DurableExitLeg:
        """Read one active exit leg from the canonical PAPER order ledger."""
        order_book = self._paper_order_book()
        if order_book is None:
            return _DurableExitLeg(role)
        try:
            identity = lifecycle_identity(state.signal)
            protective = role in {"STOP", "STOP_LOSS"}
            active = tuple(
                order
                for order in order_book.open_orders_for_symbol(state.signal.symbol)
                if
                order.request.side.value == "SELL"
                and order.request.strategy_lifecycle_id == identity
                and not order.is_terminal
                and int(order.remaining_quantity) > 0
                and (
                    (
                        protective
                        and order.request.order_type.value == "STOP"
                        and order.request.execution_reason in {"STOP", "STOP_LOSS"}
                    )
                    or (
                        not protective
                        and order.request.order_type.value == "LIMIT"
                        and order.request.execution_reason == role
                    )
                )
            )
            working_quantity = sum(
                int(order.remaining_quantity) for order in active
            )

            def quantity_matches(order: object) -> bool:
                if desired_quantity is None:
                    return True
                remaining = int(order.remaining_quantity)
                return (
                    remaining >= desired_quantity
                    if protective else remaining == desired_quantity
                )

            def price_matches(order: object) -> bool:
                if desired_price is None:
                    return True
                observed = (
                    order.request.stop_price
                    if protective else order.request.limit_price
                )
                if observed is None:
                    return False
                return (
                    Decimal(observed) >= desired_price
                    if protective else Decimal(observed) == desired_price
                )

            exact = tuple(
                order for order in active
                if quantity_matches(order) and price_matches(order)
            )
            return _DurableExitLeg(
                role=role,
                working_quantity=working_quantity,
                matching_order=exact[0] if len(active) == 1 and len(exact) == 1 else None,
                active_orders=active,
            )
        except Exception:
            return _DurableExitLeg(role)

    def _durable_target_filled_quantity(
        self, state: _PaperState, role: str,
    ) -> int:
        order_book = self._paper_order_book()
        if order_book is None:
            return 0
        try:
            identity = lifecycle_identity(state.signal)
            return sum(
                int(order.filled_quantity)
                for order in order_book.history()
                if order.symbol == state.signal.symbol
                and order.request.side.value == "SELL"
                and order.request.order_type.value == "LIMIT"
                and order.request.strategy_lifecycle_id == identity
                and order.request.execution_reason == role
            )
        except Exception:
            return 0

    def _has_target_lifecycle_mismatch(
        self, state: _PaperState, role: str,
    ) -> bool:
        order_book = self._paper_order_book()
        if order_book is None:
            return False
        try:
            identity = lifecycle_identity(state.signal)
            return any(
                order.request.side.value == "SELL"
                and order.request.order_type.value == "LIMIT"
                and order.request.execution_reason == role
                and order.request.strategy_lifecycle_id != identity
                and not order.is_terminal
                and int(order.remaining_quantity) > 0
                for order in order_book.open_orders_for_symbol(state.signal.symbol)
            )
        except Exception:
            return False

    def _paper_target_is_working(self, state: _PaperState) -> bool:
        """Return true only when the durable ledger contains an active target."""
        return any(
            self._durable_exit_leg(state, role).working_quantity > 0
            for role in ("FIRST_TARGET", "SECOND_TARGET")
        )

    def _paper_exit_order_complete(
        self, state: _PaperState, order_id: str | None,
        role: str, filled_quantity: int,
    ) -> bool:
        """Use durable order completion when the bridge exposes an order book."""
        if filled_quantity >= (
            state.first_quantity if role == "FIRST_TARGET" else state.second_quantity
        ):
            return True
        order_book = self._paper_order_book()
        if order_book is None or not order_id:
            return False
        try:
            order = order_book.get(order_id)
            return bool(order.is_terminal and int(order.remaining_quantity) == 0)
        except Exception:
            return False

    @staticmethod
    def _record_completed_bar_metric(name: str, *, count: int = 1) -> None:
        if count <= 0:
            return
        try:
            performance_diagnostics.record_component_duration(
                name, 0.0, success=True,
            )
        except Exception:
            pass

    @staticmethod
    def _record_completed_bar_duration(
        name: str,
        started: float,
        *,
        symbol: str,
        success: bool,
    ) -> None:
        try:
            performance_diagnostics.record_component_duration(
                name,
                (perf_counter() - started) * 1000.0,
                symbol=symbol,
                success=success,
            )
        except Exception:
            pass

    @staticmethod
    def _resolve_treatment_policy_enabled_from_callback(callback: object) -> bool | None:
        owner = getattr(callback, "__self__", None)
        config = getattr(owner, "config", None)
        if config is None:
            return None
        return bool(
            getattr(config, "enabled", False)
            and str(getattr(config, "mode", "")).strip().upper() == "PAPER_TREATMENT"
        )

    def _resolve_treatment_policy_enabled(self) -> bool:
        explicit = self._resolve_treatment_policy_enabled_from_callback(
            self._paper_entry_intelligence,
        )
        if explicit is not None:
            return explicit
        # A custom decision-intelligence entry callback without an explicit
        # policy object remains execution-relevant for backwards compatibility.
        return self._decision_intelligence_entry_observer is not None

    def _remember_latest_observation(
        self, symbol: str, value: PointInTimeObservation,
        account: PaperAccountContext | None,
    ) -> None:
        self._expire_pending_treatment(value.observation.timestamp)
        self._latest_observations[symbol] = (value, account)
        self._latest_observations.move_to_end(symbol)
        while len(self._latest_observations) > self._latest_observation_capacity:
            self._latest_observations.popitem(last=False)

    def _expire_pending_treatment(self, now: datetime) -> None:
        expired = [
            key for key, pending in self._pending_treatment.items()
            if (
                now - pending.created_at
            ).total_seconds() > float(self._pending_treatment_lifetime_seconds)
        ]
        for key in expired:
            pending = self._pending_treatment.pop(key)
            performance_diagnostics.record_entry_funnel(
                pending.symbol, stage="TREATMENT_PENDING_EXPIRED",
                outcome="REJECTED", reason="PENDING_LIFETIME_EXPIRED",
                timestamp=now,
            )

    def _pending_treatment_key(
        self, symbol: str, identity: _IntelligenceIdentity,
    ) -> str:
        return f"{symbol}|{identity!r}"

    def _remember_pending_treatment(
        self, identity: _IntelligenceIdentity, signal: MomentumEntrySignal,
        value: PointInTimeObservation, account: PaperAccountContext | None,
    ) -> None:
        key = self._pending_treatment_key(identity.symbol, identity)
        self._pending_treatment[key] = _PendingTreatmentSignal(
            identity=identity,
            symbol=identity.symbol,
            lifecycle_id=lifecycle_identity(signal),
            value=value,
            account=account,
            created_at=value.observation.timestamp,
        )
        self._pending_treatment.move_to_end(key)
        while len(self._pending_treatment) > self._pending_treatment_capacity:
            self._pending_treatment.popitem(last=False)

    def _on_intelligence_publication(self, publication: object) -> None:
        if not self._intelligence_redrive_enabled or not self._treatment_policy_enabled:
            return
        # A capture-only service has no execution boundary to re-drive.  Keep
        # its established publication-on-next-observation behavior; the live
        # PAPER composition supplies the submitter and therefore owns the
        # asynchronous execution re-drive.
        if self._paper_entry_submitter is None:
            return
        identity = getattr(publication, "identity", None)
        symbol = str(getattr(publication, "key", "")).strip().upper()
        if not symbol or not isinstance(identity, _IntelligenceIdentity):
            return
        result = getattr(publication, "value", None)
        if not isinstance(result, _IntelligenceResult):
            return
        key = self._pending_treatment_key(symbol, identity)
        pending = self._pending_treatment.pop(key, None)
        if pending is None or pending.identity != identity:
            return
        if not (
            (
                result.canonical_lifecycle_id == pending.lifecycle_id
                or result.treatment_lifecycle_id == pending.lifecycle_id
            )
        ):
            performance_diagnostics.record_entry_funnel(
                symbol, stage="TREATMENT_MISMATCH", outcome="REJECTED",
                reason="LIFECYCLE_OR_OPPORTUNITY_MISMATCH",
                timestamp=pending.created_at,
            )
            return
        latest = self._latest_observations.get(symbol)
        if latest is None:
            performance_diagnostics.record_entry_funnel(
                symbol, stage="TREATMENT_PENDING_EXPIRED", outcome="REJECTED",
                reason="NO_CURRENT_OBSERVATION", timestamp=pending.created_at,
            )
            return
        latest_value, latest_account = latest
        age = (
            latest_value.observation.timestamp - pending.created_at
        ).total_seconds()
        if age < 0 or age > float(self._pending_treatment_lifetime_seconds):
            performance_diagnostics.record_entry_funnel(
                symbol, stage="TREATMENT_PENDING_EXPIRED", outcome="REJECTED",
                reason="PENDING_LIFETIME_EXPIRED",
                timestamp=latest_value.observation.timestamp,
            )
            return
        performance_diagnostics.record_entry_funnel(
            symbol, stage="TREATMENT_REDRIVE", outcome="STARTED",
            timestamp=latest_value.observation.timestamp,
        )
        try:
            self.observe(
                latest_value,
                account=(latest_account if latest_account is not None else (
                    self._account_refresh_source()
                    if self._account_refresh_source is not None else pending.account
                )),
                _treatment_redrive=True,
            )
        except Exception:
            performance_diagnostics.record_entry_funnel(
                symbol, stage="TREATMENT_REDRIVE_REJECTED", outcome="REJECTED",
                reason="CURRENT_STATE_REVALIDATION_FAILED",
                timestamp=latest_value.observation.timestamp,
            )

    @staticmethod
    def _completed_bar_version(value: object) -> tuple[object, ...]:
        observation = value.observation
        symbol = str(observation.symbol).strip().upper()
        cutoff = observation.timestamp
        bars = tuple(
            (
                bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume,
            )
            for bar in value.bars
            if str(bar.symbol).strip().upper() == symbol
            and bar.timestamp + timedelta(minutes=1) <= cutoff
        )
        return str(value.session).upper(), cutoff.replace(second=0, microsecond=0), bars

    @staticmethod
    def _intelligence_identity(
        value: object, candidate: MomentumCandidate,
        completed_bar_version: tuple[object, ...],
    ) -> _IntelligenceIdentity:
        setup = candidate.setup
        setup_version = () if setup is None else (
            setup.setup_type.value,
            setup.state.value,
            setup.taxonomy_strategy_id,
            setup.taxonomy_opportunity_id,
            setup.taxonomy_opportunity_anchor,
            setup.structural_episode_id,
            setup.structural_anchor,
            setup.trigger,
            setup.stop_price,
        )
        observation = value.observation
        return _IntelligenceIdentity(
            symbol=str(observation.symbol).strip().upper(),
            trading_date=observation.timestamp.date(),
            session=str(value.session).upper(),
            completed_bar_version=completed_bar_version,
            setup_version=setup_version,
        )

    def _evaluate_intelligence_serialized(
        self, request: _IntelligenceRequest,
    ) -> _IntelligenceResult:
        symbol = request.identity.symbol
        taxonomy_candidate = None
        taxonomy_signal = None
        bridge = self._taxonomy_execution_bridge
        with _service_stage("taxonomy_async", symbol=symbol):
            if bridge is not None:
                taxonomy_candidate, taxonomy_signal = bridge.evaluate(
                    request.value,
                    request.candidate,
                    request.signal,
                    request.stale,
                    self.runtime,
                    self.capture_config.quote_stale_after_seconds,
                )
        del taxonomy_signal
        intelligence_candidate = (
            taxonomy_candidate
            if taxonomy_candidate is not None and request.signal is None
            else request.candidate
        )
        intelligence_result = None
        treatment_signal = None
        treatment_decision = None
        policy_completed = False
        with _service_stage("decision_intelligence_async", symbol=symbol):
            if self._decision_intelligence_entry_observer is not None:
                try:
                    observed = self._decision_intelligence_entry_observer(
                        value=request.value,
                        candidate=intelligence_candidate,
                        signal=request.signal,
                        taxonomy_candidate=taxonomy_candidate,
                        legacy_candidate=request.legacy_candidate,
                        decision_timestamp=(
                            request.value.evaluation_timestamp
                            or request.value.observation.timestamp
                        ),
                    )
                    intelligence_result = observed[0]
                    treatment_signal = observed[1]
                    treatment_decision = observed[2] if len(observed) > 2 else None
                    policy_completed = True
                except Exception:
                    intelligence_result, treatment_signal, treatment_decision = None, None, None
            elif self._decision_intelligence_observer is not None:
                try:
                    intelligence_result = self._decision_intelligence_observer(
                        value=request.value,
                        candidate=intelligence_candidate,
                        signal=request.signal,
                        taxonomy_candidate=taxonomy_candidate,
                        legacy_candidate=request.legacy_candidate,
                    )
                except Exception:
                    intelligence_result = None
                if self._paper_entry_intelligence is not None:
                    try:
                        treatment_decision, treatment_signal = self._paper_entry_intelligence(
                            result=intelligence_result,
                            candidate=intelligence_candidate,
                            environment="PAPER",
                            signal_factory=self.runtime.entry_signal,
                            decision_timestamp=(
                                request.value.evaluation_timestamp
                                or request.value.observation.timestamp
                            ),
                            existing_signal=request.signal,
                        )
                        policy_completed = True
                    except Exception:
                        treatment_signal = None
        linked_signal = (
            treatment_signal
            if treatment_signal is not None
            else request.signal if policy_completed else None
        )
        if (
            linked_signal is None
            and treatment_decision is not None
            and request.candidate.setup is not None
        ):
            try:
                linked_signal = self.runtime.entry_signal(replace(
                    request.candidate,
                    setup=replace(
                        request.candidate.setup, state=SetupState.TRIGGERED,
                    ),
                ))
            except Exception:
                linked_signal = None
        if (
            linked_signal is not None
            and treatment_decision is not None
            and not self._link_assignment_background(
                treatment_decision, linked_signal,
            )
        ):
            linked_signal = None
        linked_lifecycle = (
            None if linked_signal is None else lifecycle_identity(linked_signal)
        )
        if linked_lifecycle is not None:
            current = tuple(
                value for value in self._linked_treatment_lifecycles
                if value != linked_lifecycle
            )
            self._linked_treatment_lifecycles = (
                *current[-127:], linked_lifecycle,
            )
        return _IntelligenceResult(
            intelligence_result=intelligence_result,
            treatment_signal=treatment_signal,
            treatment_setup=(
                None if treatment_signal is None
                else getattr(intelligence_candidate, "setup", None)
            ),
            taxonomy_candidate=taxonomy_candidate,
            treatment_decision=treatment_decision,
            treatment_lifecycle_id=linked_lifecycle,
            canonical_signal_present=request.signal is not None,
            canonical_lifecycle_id=(
                None if request.signal is None else lifecycle_identity(request.signal)
            ),
        )

    def _link_assignment_background(
        self, decision: object, signal: object,
    ) -> bool:
        policy = getattr(self._paper_entry_intelligence, "__self__", None)
        if policy is None:
            callback_owner = getattr(
                self._decision_intelligence_entry_observer, "__self__", None,
            )
            policy = getattr(callback_owner, "_paper_entry_intelligence", None)
        linker = getattr(policy, "link_assignment_lifecycle", None)
        if not callable(linker):
            return False
        try:
            return bool(linker(decision, signal))
        except Exception:
            return False

    def _current_treatment_signal(
        self, result: _IntelligenceResult, candidate: MomentumCandidate,
        *, stale: bool,
    ) -> MomentumEntrySignal | None:
        cached_signal = result.treatment_signal
        cached_setup = result.treatment_setup
        if stale or cached_signal is None or cached_setup is None:
            return None
        trigger = getattr(cached_setup, "trigger", None)
        stop = getattr(cached_setup, "stop_price", None)
        if trigger is None or stop is None or stop >= trigger or candidate.price <= ZERO:
            return None
        # Price-sensitive entry quality is always recomputed from this event;
        # no price, quote, spread, or freshness value is reused from the cache.
        if candidate.price > trigger or (trigger - candidate.price) / candidate.price > Decimal("0.01"):
            return None
        current_setup = candidate.setup
        if current_setup is not None:
            if current_setup.setup_type != cached_setup.setup_type:
                return None
            for name in ("taxonomy_opportunity_id", "structural_episode_id"):
                current_id = getattr(current_setup, name, None)
                cached_id = getattr(cached_setup, name, None)
                if current_id is not None and cached_id is not None and current_id != cached_id:
                    return None
            if (
                current_setup.trigger not in (None, trigger)
                or current_setup.stop_price not in (None, stop)
            ):
                return None
            cached_setup = current_setup
        try:
            projected = replace(
                candidate,
                setup=replace(cached_setup, state=SetupState.TRIGGERED),
            )
            fresh_signal = self.runtime.entry_signal(projected)
            if fresh_signal is None:
                return None
            decision = result.treatment_decision
            if (
                decision is not None
                and result.treatment_lifecycle_id
                != lifecycle_identity(fresh_signal)
            ):
                return None
            return fresh_signal
        except Exception:
            return None

    def intelligence_worker_metrics(self) -> IntelligenceWorkerMetrics:
        handoff = self._intelligence_handoff
        return IntelligenceWorkerMetrics() if handoff is None else handoff.metrics()

    def wait_for_intelligence(self, *, timeout_seconds: float = 5.0) -> bool:
        handoff = self._intelligence_handoff
        return True if handoff is None else handoff.wait_idle(timeout_seconds)

    def close_intelligence_worker(self, *, timeout_seconds: float = 5.0) -> bool:
        self._intelligence_redrive_enabled = False
        self._pending_treatment.clear()
        handoff = self._intelligence_handoff
        if handoff is None:
            return True
        return handoff.stop(drain=True, timeout_seconds=timeout_seconds)

    def completed_bar_metrics(self) -> CompletedBarProcessingMetrics:
        handoff = self._completed_bar_research_handoff
        return CompletedBarProcessingMetrics(
            cache_hit=self._completed_bar_cache_hits,
            cache_miss=self._completed_bar_cache_misses,
            new_bars_processed=self._completed_bar_new_bars_processed,
            duplicate_bars_suppressed=(
                self._completed_bar_duplicate_bars_suppressed
            ),
            lifecycle_catchup_bars=self._completed_bar_lifecycle_catchup_bars,
            research_submit=self._completed_bar_research_submits,
            research_duplicate_suppressed=(
                self._completed_bar_research_duplicate_suppressed
            ),
            research_worker=(
                CompletedBarResearchMetrics()
                if handoff is None else handoff.metrics()
            ),
        )

    def wait_for_completed_bar_research(
        self, *, timeout_seconds: float = 5.0,
    ) -> bool:
        handoff = self._completed_bar_research_handoff
        return True if handoff is None else handoff.wait_idle(timeout_seconds)

    def close_completed_bar_research(
        self, *, timeout_seconds: float = 5.0,
    ) -> bool:
        handoff = self._completed_bar_research_handoff
        if handoff is None:
            return True
        return handoff.stop(drain=True, timeout_seconds=timeout_seconds)

    def observe_market_bar(self, symbol: str, bar: MinuteBar, observed_at) -> None:
        """Advance retained paper/counterfactual state independent of ranking."""
        normalized = symbol.strip().upper()
        records: list[CaptureRecord] = []
        bar_key = (normalized, bar.timestamp)
        if bar_key not in self._seen_bars:
            records.append(_bar_record(bar, observed_at))
            self._seen_bars.add(bar_key)
        state = self._paper.get(normalized)
        if state is not None and bar.timestamp >= state.signal.timestamp:
            position_changed = False
            if self._paper_position_quantity_source is not None:
                try:
                    position_changed = (
                        int(self._paper_position_quantity_source(normalized))
                        != int(state.remaining)
                    )
                except Exception:
                    position_changed = False
            if (
                state.last_bar_timestamp is None
                or bar.timestamp > state.last_bar_timestamp
                or position_changed
            ):
                records.extend(self._advance_paper(state, bar, observed_at))
        counter = self._counterfactual.get(normalized)
        if counter is not None:
            if counter.last_bar_timestamp is None or bar.timestamp > counter.last_bar_timestamp:
                records.extend(
                    self._advance_counterfactual(counter, bar, observed_at),
                )
        if self._shadow is not None:
            records.extend(self._shadow.observe_bar(bar))
        if records:
            self._submit_records(tuple(records))

    def _advance_counterfactual(
        self, counter: _CounterState, bar: MinuteBar, observed_at: datetime,
    ) -> tuple[CaptureRecord, ...]:
        counter.bars_observed += 1
        counter.last_bar_timestamp = bar.timestamp
        risk = counter.trigger - counter.stop
        records = [CaptureRecord.create(
            CaptureRecordType.COUNTERFACTUAL, counter.symbol, observed_at,
            {"action": "PATH", "source_bar_timestamp": bar.timestamp,
             "open": bar.open, "high": bar.high, "low": bar.low,
             "close": bar.close, "volume": bar.volume,
             "high_r": None if risk <= 0 else (bar.high - counter.trigger) / risk,
             "low_r": None if risk <= 0 else (bar.low - counter.trigger) / risk,
             "bars_observed": counter.bars_observed},
            identity_parts=(bar.timestamp.isoformat(),),
        )]
        if counter.bars_observed >= self.capture_config.counterfactual_bars:
            records.append(CaptureRecord.create(
                CaptureRecordType.COUNTERFACTUAL, counter.symbol, observed_at,
                {"action": "END", "bars_observed": counter.bars_observed},
                identity_parts=("END", str(counter.started_at)),
            ))
            self._counterfactual.pop(counter.symbol, None)
        return tuple(records)

    def finalize_shadow_outcomes(self, observed_at: datetime) -> None:
        """Persist due incomplete windows without granting execution authority."""
        if self._shadow is not None:
            self._submit_records(self._shadow.finalize_due(observed_at))

    def _submit_records(self, records: Iterable[CaptureRecord]) -> None:
        """Attach campaign provenance to new active PAPER lifecycle records."""
        prepared: list[CaptureRecord] = []
        for record in records:
            if (
                self.paper_campaign_id is not None
                and record.record_type in {
                    CaptureRecordType.PAPER_FILL,
                    CaptureRecordType.MANAGEMENT_CONTEXT,
                }
                and record.payload.get("paper_campaign_id") != self.paper_campaign_id
            ):
                payload = record.payload
                payload["paper_campaign_id"] = self.paper_campaign_id
                record = CaptureRecord.create(
                    record.record_type, record.symbol, record.timestamp, payload,
                    identity_parts=(record.record_id, "paper-campaign"),
                )
            prepared.append(record)
        if not prepared:
            return
        handoff = self._observation_record_handoff
        if handoff is None:
            self.writer.submit_many(tuple(prepared))
            return
        critical_types = {
            CaptureRecordType.PAPER_FILL,
            CaptureRecordType.MANAGEMENT_CONTEXT,
            CaptureRecordType.EXECUTION_PRICE_PATH,
        }
        critical = tuple(
            record for record in prepared if record.record_type in critical_types
        )
        observational = tuple(
            record for record in prepared if record.record_type not in critical_types
        )
        if critical:
            self.writer.submit_many(critical)
        if observational:
            # Latest state per symbol/type is sufficient for observational
            # evidence and prevents capture I/O from delaying strategy work.
            keys = {f"{record.symbol}:{record.record_type.value}" for record in observational}
            for key in keys:
                values = tuple(
                    record for record in observational
                    if f"{record.symbol}:{record.record_type.value}" == key
                )
                if not handoff.submit(key, values):
                    # A saturated diagnostic handoff is explicitly
                    # observational; retain correctness by falling back to
                    # the existing bounded writer path.
                    self.writer.submit_many(values)

    def _dispatch_observation_records(self, value: object) -> None:
        try:
            records = tuple(value) if isinstance(value, tuple) else ()
            if records:
                self.writer.submit_many(records)
        except Exception:
            # Capture failure must not affect strategy or execution state.
            return

    def close_observation_records(self) -> None:
        handoff = self._observation_record_handoff
        if handoff is not None:
            handoff.stop(drain=True)

    def _evidence_records(self, value: PointInTimeObservation) -> tuple[CaptureRecord, ...]:
        observation = value.observation
        midpoint = spread_dollars = spread_percent = None
        if observation.bid is not None and observation.ask is not None:
            midpoint = (observation.bid + observation.ask) / Decimal("2")
            spread_dollars = observation.ask - observation.bid
            spread_percent = spread_dollars / midpoint * HUNDRED
        catalyst = CaptureRecord.create(
            CaptureRecordType.CATALYST_EVIDENCE, observation.symbol, observation.timestamp,
            {"evidence_state": observation.catalyst_status.value,
             "event_type": observation.catalyst.value,
             "event_timestamp": value.catalyst_event_timestamp,
             "event_date": value.catalyst_event_date,
             "observation_timestamp": observation.timestamp,
             "source": value.catalyst_source,
             "source_classification": value.catalyst_source_classification},
        )
        spread = CaptureRecord.create(
            CaptureRecordType.SPREAD_EVIDENCE, observation.symbol, observation.timestamp,
            {"bid": observation.bid, "ask": observation.ask, "midpoint": midpoint,
             "spread_dollars": spread_dollars, "spread_percent": spread_percent,
             "observation_timestamp": value.quote_observed_at or observation.timestamp,
             "freshness_seconds": value.quote_freshness_seconds,
             "last_price_observation_timestamp": value.last_price_observed_at,
             "last_price_freshness_seconds": value.last_price_freshness_seconds,
             "authoritative": observation.bid is not None and observation.ask is not None},
        )
        return catalyst, spread

    def _transition_records(
        self, candidate: MomentumCandidate, signal: MomentumEntrySignal | None,
        *, account: PaperAccountContext | None,
    ) -> tuple[CaptureRecord, ...]:
        symbol = candidate.symbol
        transitions: list[ForwardTransition] = []
        if symbol not in self._last_transition:
            transitions.append(ForwardTransition.DISCOVERED)
        mapping = {
            CandidateStatus.DISCOVERED: ForwardTransition.DISCOVERED,
            CandidateStatus.WATCH: ForwardTransition.WATCH,
            CandidateStatus.NEAR_QUALIFIED: ForwardTransition.NEAR,
            CandidateStatus.QUALIFIED: ForwardTransition.QUALIFIED,
            CandidateStatus.SETUP_FORMING: ForwardTransition.SETUP_FORMING,
            CandidateStatus.ENTRY_READY: ForwardTransition.ENTRY_READY,
            CandidateStatus.AWAITING_EXECUTION_DATA: ForwardTransition.AWAITING_EXECUTION_DATA,
            CandidateStatus.INELIGIBLE_FOR_EXECUTION: ForwardTransition.ENTRY_BLOCKED,
        }
        if candidate.setup is not None and candidate.setup.state is SetupState.TRIGGERED:
            terminal = (
                ForwardTransition.ENTRY_READY if signal is not None
                else ForwardTransition.AWAITING_EXECUTION_DATA
                if candidate.status is CandidateStatus.AWAITING_EXECUTION_DATA
                else ForwardTransition.ENTRY_BLOCKED
            )
            if self._last_transition.get(symbol) is terminal:
                return ()
            transitions.append(ForwardTransition.SETUP_TRIGGERED)
            transitions.append(terminal)
        else:
            transitions.append(mapping[candidate.status])
        records: list[CaptureRecord] = []
        for transition in transitions:
            if self._last_transition.get(symbol) is transition:
                continue
            records.append(_transition_record(
                candidate, transition,
                tuple(
                    code.value
                    for code in entry_rejections(candidate, self.config)
                ),
                (
                    _gate_diagnostics(candidate, self.config, account=account)
                    if transition is ForwardTransition.ENTRY_BLOCKED else ()
                ),
            ))
            self._last_transition[symbol] = transition
        return tuple(records)

    def _update_peak(self, state: _PaperState) -> None:
        risk = state.signal.risk_per_share
        if state.maximum_high is None or risk <= ZERO:
            return
        prior_peak = state.peak_r
        prior_defense_armed = state.profit_defense_armed
        state.peak_price = state.maximum_high
        state.peak_r = (state.peak_price - state.entry_price) / risk
        if state.current_r is not None:
            state.giveback_r = state.peak_r - state.current_r
            state.giveback_fraction_of_peak = (
                None if state.peak_r <= ZERO else state.giveback_r / state.peak_r
            )
        state.profit_defense_armed = (
            self.config.trade_management.profit_defense_enabled
            and state.peak_r >= self.config.trade_management.profit_defense_activation_r
        )
        if prior_peak is None or state.peak_r > prior_peak:
            self._record_management_event(
                state, "PEAK_R_UPDATED", timestamp=state.last_bar_timestamp,
            )
        if not prior_defense_armed and state.profit_defense_armed:
            self._record_management_event(
                state, "PROFIT_DEFENSE_ARMED", timestamp=state.last_bar_timestamp,
            )

    def _record_management_event(
        self, state: _PaperState, event: str, *, timestamp: datetime | None = None,
        **values: object,
    ) -> None:
        """Publish bounded, sanitized exit-management observability."""
        try:
            target_role = (
                "FIRST_TARGET" if not state.first_taken else
                "SECOND_TARGET" if not state.second_taken else None
            )
            desired_target_qty = int(values.pop(
                "desired_target_qty",
                min(
                    state.first_quantity
                    if target_role == "FIRST_TARGET" else state.second_quantity,
                    state.remaining,
                ) if target_role is not None else 0,
            ))
            desired_target_price = values.pop("desired_target_price", None)
            if desired_target_price is None and target_role is not None:
                try:
                    desired_target_price = state.signal.target_levels[
                        0 if target_role == "FIRST_TARGET" else 1
                    ]
                except (AttributeError, IndexError, TypeError):
                    desired_target_price = None
            target_leg = (
                self._durable_exit_leg(state, target_role)
                if target_role is not None else _DurableExitLeg("RUNNER")
            )
            stop_leg = self._durable_exit_leg(state, "STOP")
            # A caller cannot override a field named "working": those values
            # are facts read from the canonical durable ledger only.
            values.pop("target_working_qty", None)
            values.pop("actual_target_working_qty", None)
            values.pop("stop_working_qty", None)
            performance_diagnostics.record_management_event(
                state=event,
                symbol=state.signal.symbol,
                lifecycle_id=lifecycle_identity(state.signal),
                authoritative_position_qty=state.remaining,
                desired_target_qty=desired_target_qty,
                desired_target_price=desired_target_price,
                actual_target_working_qty=target_leg.working_quantity,
                target_working_qty=target_leg.working_quantity,
                actual_target_working_order_id=getattr(
                    target_leg.matching_order, "order_id", None,
                ),
                stop_working_qty=stop_leg.working_quantity,
                remaining_qty=state.remaining,
                current_r=state.current_r,
                peak_r=state.peak_r,
                giveback_r=state.giveback_r,
                timestamp=timestamp or state.last_bar_timestamp,
                **values,
            )
        except Exception:
            # Diagnostics are strictly observational and cannot affect exits.
            return

    def _capture_exit_evidence(
        self, state: _PaperState, value: PointInTimeObservation,
    ) -> None:
        observation = value.observation
        stale = self.capture_config.quote_stale_after_seconds
        quote_fresh = (
            value.quote_freshness_seconds is not None
            and value.last_price_freshness_seconds is not None
            and ZERO <= value.quote_freshness_seconds <= stale
            and ZERO <= value.last_price_freshness_seconds <= stale
        )
        depth = depth_features(value.depth_bids, value.depth_asks)
        flow = value.order_flow
        state.latest_exit_evidence = AdaptiveExitEvidence(
            evaluated_at=value.evaluation_timestamp or observation.timestamp,
            quote_fresh=quote_fresh,
            best_bid=observation.bid,
            best_ask=observation.ask,
            depth_imbalance=None if depth is None else depth.imbalance,
            microprice=None if depth is None else depth.microprice,
            flow_classification=(
                None if flow is None else str(flow.classification.value)
            ),
            flow_fresh=bool(flow is not None and flow.fresh),
        )

    def _install_rearmed_paper_state(
        self, signal: MomentumEntrySignal, shares: int,
        risk_dollars: Decimal, observed_at: datetime,
    ) -> None:
        """Bind an authorized rearm to management before its first fill."""
        prior = self._paper.get(signal.symbol)
        records: list[CaptureRecord] = []
        if prior is not None:
            records.append(_management_context_record(
                signal.symbol, observed_at, prior.signal, prior,
                phase="SUPERSEDED",
            ))
        first = int((
            Decimal(shares)
            * self.config.trade_management.first_target_exit_percent
        ).to_integral_value(rounding=ROUND_FLOOR))
        second = int((
            Decimal(shares)
            * self.config.trade_management.second_target_exit_percent
        ).to_integral_value(rounding=ROUND_FLOOR))
        state = _PaperState(
            signal, signal.entry_trigger, shares, 0, signal.stop_price,
            first, second, 0, risk_budget=risk_dollars,
            initial_stop=signal.stop_price,
        )
        self._paper[signal.symbol] = state
        self._last_transition[signal.symbol] = ForwardTransition.PAPER_ENTRY
        records.extend((
            CaptureRecord.create(
                CaptureRecordType.STATE_TRANSITION,
                signal.symbol,
                observed_at,
                {
                    "from": ForwardTransition.PAPER_EXIT.value,
                    "to": ForwardTransition.PAPER_ENTRY.value,
                    "reason_codes": ["REARMED_ENTRY_WORKING"],
                    "lifecycle_id": lifecycle_identity(signal),
                },
                identity_parts=(
                    ForwardTransition.PAPER_ENTRY.value,
                    lifecycle_identity(signal),
                    "REARMED",
                ),
            ),
            _management_context_record(
                signal.symbol, observed_at, signal, state,
                phase="ENTRY_WORKING",
            ),
        ))
        self._submit_records(tuple(records))

    def _authoritative_average_entry(
        self, state: _PaperState,
    ) -> Decimal | None:
        order_book = self._paper_order_book()
        if order_book is None:
            return None
        try:
            identity = lifecycle_identity(state.signal)
            entries = tuple(
                order for order in order_book.history()
                if order.symbol == state.signal.symbol
                and order.request.side.value == "BUY"
                and order.request.strategy_lifecycle_id == identity
                and order.filled_quantity > 0
                and order.average_fill_price is not None
            )
            quantity = sum(
                (Decimal(order.filled_quantity) for order in entries), ZERO,
            )
            if quantity <= ZERO:
                return None
            notional = sum((
                Decimal(order.filled_quantity) * order.average_fill_price
                for order in entries
            ), ZERO)
            return notional / quantity
        except Exception:
            return None

    def _latest_exit_fill_price(
        self, state: _PaperState, role: str | None,
    ) -> Decimal | None:
        order_book = self._paper_order_book()
        if order_book is None:
            return None
        try:
            identity = lifecycle_identity(state.signal)
            fills = tuple(
                order for order in order_book.history()
                if order.symbol == state.signal.symbol
                and order.request.side.value == "SELL"
                and order.request.strategy_lifecycle_id == identity
                and order.filled_quantity > 0
                and order.average_fill_price is not None
                and (
                    role is None
                    or order.request.execution_reason == role
                    or self._exit_role_for_reason(
                        order.request.execution_reason
                    ) == role
                )
            )
            if not fills:
                return None
            return max(
                fills, key=lambda order: (order.updated_at, order.order_id)
            ).average_fill_price
        except Exception:
            return None

    def _synchronize_authoritative_position(
        self, state: _PaperState, observed_at: datetime,
    ) -> bool:
        """Adopt fills and establish canonical protection on streaming ticks."""
        if self._paper_position_quantity_source is None:
            return False
        try:
            quantity = max(
                0, int(self._paper_position_quantity_source(state.signal.symbol)),
            )
        except Exception:
            return False
        if quantity <= 0:
            return False
        changed = False
        first_authoritative_position = not state.authoritative_position_seen
        previous = state.remaining if state.authoritative_position_seen else 0
        average = self._authoritative_average_entry(state)
        if average is not None and average > ZERO and average != state.entry_price:
            state.entry_price = average
            changed = True
        if state.initial_stop is None:
            state.initial_stop = state.signal.stop_price
        if not state.authoritative_position_seen:
            state.authoritative_position_seen = True
            changed = True
        quantity_changed = quantity != state.remaining
        if quantity_changed:
            if quantity < previous:
                role, order_id = self._observed_exit_role(state)
                filled = previous - quantity
                fill_price = self._latest_exit_fill_price(state, role)
                if fill_price is None and state.latest_exit_evidence is not None:
                    fill_price = state.latest_exit_evidence.best_bid
                if fill_price is not None:
                    realized = (fill_price - state.entry_price) * filled
                    state.realized_pnl += realized
                    state.realized_from_partials += realized
                if role and role.startswith("PROFIT_HARVEST_"):
                    try:
                        state.profit_harvest_stage = max(
                            state.profit_harvest_stage,
                            int(role.rsplit("_", 1)[-1]),
                        )
                    except ValueError:
                        pass
                    state.pending_profit_harvest_role = None
                    state.exit_reason = None
                    state.exit_price = None
                    self._record_management_event(
                        state, "PROFIT_HARVEST_FILLED",
                        timestamp=observed_at, fill_quantity=filled,
                        order_id=order_id,
                    )
            state.remaining = quantity
            changed = True
        state.managed_quantity = (
            quantity if first_authoritative_position
            else max(state.managed_quantity, quantity)
        )
        if not state.first_taken and not state.second_taken:
            state.first_quantity = int((
                Decimal(quantity)
                * self.config.trade_management.first_target_exit_percent
            ).to_integral_value(rounding=ROUND_FLOOR))
            state.second_quantity = int((
                Decimal(quantity)
                * self.config.trade_management.second_target_exit_percent
            ).to_integral_value(rounding=ROUND_FLOOR))
        order_book_available = self._paper_order_book() is not None
        stop_leg = self._durable_exit_leg(
            state, "STOP", desired_quantity=quantity,
            desired_price=state.stop,
        )
        stop_already_proven = (
            not order_book_available
            and state.protection_reconciled
            and not quantity_changed
        )
        if not stop_leg.is_exact and not stop_already_proven:
            result = self._submit_exit(
                state, state.stop, quantity, "STOP",
            )
            active = (
                result.protection_active
                if isinstance(result, PaperExitSubmissionDecision)
                else bool(result)
            )
            state.protection_reconciled = active
            if active:
                state.protective_stop_activated_at = (
                    getattr(result, "activation_timestamp", None)
                    or observed_at
                )
                self._record_management_event(
                    state, "PROTECTION_ACTIVE", timestamp=observed_at,
                )
                changed = True
        else:
            state.protection_reconciled = True
        if not state.entry_fill_recorded:
            state.entry_fill_recorded = True
            self._submit_records((CaptureRecord.create(
                CaptureRecordType.PAPER_FILL,
                state.signal.symbol,
                observed_at,
                {
                    "action": "ENTRY",
                    "entry_authority": "AUTHORITATIVE_PAPER_LEDGER",
                    "session": state.signal.session,
                    "momentum_score": state.signal.momentum_score,
                    "setup": state.signal.setup_type.value,
                    "lifecycle_id": lifecycle_identity(state.signal),
                    "entry_trigger": state.signal.entry_trigger,
                    "fill_price": state.entry_price,
                    "structural_stop": state.initial_stop,
                    "stop_model": state.signal.stop_model.value,
                    "risk_per_share": state.entry_price - state.initial_stop,
                    "planned_shares": state.initial_quantity,
                    "filled_shares": quantity,
                    "risk_dollars": state.risk_budget,
                    "targets": state.signal.target_levels,
                    "catalyst_state": state.signal.catalyst_state.value,
                    "relative_volume": state.signal.relative_volume,
                    "float_shares": state.signal.float_shares,
                    "spread_percent": state.signal.spread_percent,
                    "authority": "AUTHORITATIVE_PAPER_LEDGER",
                },
                identity_parts=(
                    "ENTRY", lifecycle_identity(state.signal),
                    "AUTHORITATIVE",
                ),
            ),))
            changed = True
        return changed

    def _update_executable_profit_state(self, state: _PaperState) -> bool:
        evidence = state.latest_exit_evidence
        if (
            evidence is None or not evidence.quote_fresh
            or evidence.best_bid is None or evidence.best_bid <= ZERO
            or not state.authoritative_position_seen
        ):
            return False
        bid = evidence.best_bid
        prior = state.peak_executable_bid
        state.peak_executable_bid = (
            bid if prior is None else max(prior, bid)
        )
        risk = state.entry_price - (
            state.initial_stop
            if state.initial_stop is not None else state.signal.stop_price
        )
        if risk > ZERO:
            state.peak_executable_r = (
                state.peak_executable_bid - state.entry_price
            ) / risk
            state.current_r = (bid - state.entry_price) / risk
            state.giveback_r = max(
                ZERO, state.peak_executable_r - state.current_r,
            )
            state.giveback_fraction_of_peak = (
                None if state.peak_executable_r <= ZERO
                else state.giveback_r / state.peak_executable_r
            )
        state.peak_executable_pnl = max(
            ZERO,
            (state.peak_executable_bid - state.entry_price)
            * Decimal(max(state.managed_quantity, state.remaining)),
        )
        current_open = max(
            ZERO, (bid - state.entry_price) * Decimal(state.remaining),
        )
        state.peak_to_current_giveback = max(
            ZERO,
            state.peak_executable_pnl
            - state.realized_from_partials
            - current_open,
        )
        state.current_secured_profit = max(
            ZERO,
            state.realized_from_partials
            + max(
                ZERO,
                (state.stop - state.entry_price) * Decimal(state.remaining),
            ),
        )
        state.profit_defense_armed = bool(
            self.config.trade_management.profit_defense_enabled
            and state.peak_executable_r is not None
            and state.peak_executable_r
            >= self.config.trade_management.profit_defense_activation_r
        )
        return prior is None or state.peak_executable_bid > prior

    def _profit_lock_fraction(self, peak_r: Decimal) -> Decimal:
        config = self.config.trade_management
        if peak_r >= config.exceptional_profit_harvest_activation_r:
            return config.exceptional_peak_retention_fraction
        if peak_r >= config.strong_profit_harvest_activation_r:
            return config.strong_peak_retention_fraction
        return config.moderate_peak_retention_fraction

    def _manage_executable_profit_harvest(
        self, state: _PaperState, observed_at: datetime,
    ) -> bool:
        config = self.config.trade_management
        evidence = state.latest_exit_evidence
        peak_r = state.peak_executable_r
        if (
            not config.profit_harvest_enabled
            or not state.authoritative_position_seen
            or not state.protection_reconciled
            or state.remaining <= 0
            or evidence is None or not evidence.quote_fresh
            or evidence.best_bid is None
            or peak_r is None
            or peak_r < config.profit_harvest_activation_r
        ):
            return False
        changed = False
        bid = evidence.best_bid
        stage = state.profit_harvest_stage
        role: str | None = None
        quantity = 0
        if state.pending_profit_harvest_role is None:
            if stage < 1:
                role = "PROFIT_HARVEST_1"
                quantity = int((
                    Decimal(state.managed_quantity)
                    * config.profit_harvest_fraction
                ).to_integral_value(rounding=ROUND_FLOOR))
            elif (
                stage < 2
                and peak_r >= config.strong_profit_harvest_activation_r
            ):
                role = "PROFIT_HARVEST_2"
                quantity = int((
                    Decimal(state.managed_quantity)
                    * config.strong_profit_harvest_fraction
                ).to_integral_value(rounding=ROUND_FLOOR))
            elif (
                stage < 3
                and peak_r >= config.exceptional_profit_harvest_activation_r
            ):
                role = "PROFIT_HARVEST_3"
                runner_cap = max(1, int((
                    Decimal(state.managed_quantity)
                    * config.exceptional_runner_max_fraction
                ).to_integral_value(rounding=ROUND_FLOOR)))
                quantity = max(0, state.remaining - runner_cap)
            quantity = min(max(0, quantity), max(0, state.remaining - 1))
        if role is not None and quantity > 0:
            result = self._submit_exit(
                state, bid, quantity, role,
            )
            active = (
                result.state in {
                    PaperExitSubmissionState.SUBMITTED,
                    PaperExitSubmissionState.WORKING,
                    PaperExitSubmissionState.COMPLETED,
                }
                if isinstance(result, PaperExitSubmissionDecision)
                else bool(result)
            )
            if active:
                state.pending_profit_harvest_role = role
                state.exit_reason = role
                state.exit_price = bid
                self._record_management_event(
                    state, "PROFIT_HARVEST_SUBMITTED",
                    timestamp=observed_at, desired_target_qty=quantity,
                    desired_target_price=bid, harvest_stage=role,
                )
                changed = True

        initial_stop = (
            state.initial_stop
            if state.initial_stop is not None else state.signal.stop_price
        )
        risk = state.entry_price - initial_stop
        retention = self._profit_lock_fraction(peak_r)
        desired_stop = min(
            bid,
            state.entry_price
            + (state.peak_executable_bid - state.entry_price) * retention,
        )
        minimum_step = max(
            Decimal("0.01"), risk * config.profit_lock_minimum_step_r,
        )
        if desired_stop >= state.stop + minimum_step:
            target_role = state.active_exit_role
            target_order_id = state.active_exit_order_id
            target_reason = state.exit_reason
            target_price = state.exit_price
            result = self._submit_exit(
                state, desired_stop, state.remaining, "STOP",
            )
            active = (
                result.protection_active
                if isinstance(result, PaperExitSubmissionDecision)
                else bool(result)
            )
            if active:
                state.stop = max(state.stop, desired_stop)
                state.current_secured_profit = max(
                    state.current_secured_profit,
                    state.realized_from_partials
                    + max(
                        ZERO,
                        (state.stop - state.entry_price)
                        * Decimal(state.remaining),
                    ),
                )
                state.profit_defense_stop_tightened = True
                state.profit_defense_last_action = "PEAK_PROFIT_LOCK"
                if target_reason and target_reason.startswith("PROFIT_HARVEST_"):
                    state.active_exit_role = target_role
                    state.active_exit_order_id = target_order_id
                    state.exit_reason = target_reason
                    state.exit_price = target_price
                self._record_management_event(
                    state, "PEAK_PROFIT_LOCK_ACTIVE",
                    timestamp=observed_at, desired_stop=desired_stop,
                    peak_executable_bid=state.peak_executable_bid,
                    peak_executable_pnl=state.peak_executable_pnl,
                    peak_executable_r=state.peak_executable_r,
                )
                changed = True
        return changed

    def _record_management_range(
        self, state: _PaperState, bar: MinuteBar,
    ) -> None:
        value = max(ZERO, bar.high - bar.low)
        if state.prior_close is not None:
            value = max(
                value,
                abs(bar.high - state.prior_close),
                abs(bar.low - state.prior_close),
            )
        lookback = self.config.trade_management.exit_range_lookback
        state.recent_ranges = (*state.recent_ranges, value)[-lookback:]
        state.prior_close = bar.close

    def _profit_defense_action(
        self, state: _PaperState, bar: MinuteBar,
    ) -> tuple[Decimal, int, str] | None:
        config = self.config.trade_management
        if not config.profit_defense_enabled or state.remaining <= 0:
            return None
        self._update_peak(state)
        risk = state.signal.risk_per_share
        if risk <= ZERO:
            return None
        current_r = (bar.close - state.entry_price) / risk
        state.current_r = current_r
        self._update_peak(state)
        bearish_close = bar.close < bar.open
        lower_than_prior = state.prior_low is not None and bar.close < state.prior_low
        lower_high = state.maximum_high is not None and bar.high < state.maximum_high

        structural_stop = state.signal.structural_stop_price
        if (
            structural_stop is not None
            and bar.close < structural_stop
            and bearish_close
        ):
            return bar.close, state.remaining, "STRUCTURAL_CLOSE_INVALIDATION"

        if (
            not config.adaptive_exit_enabled
            or not state.profit_defense_armed
            or state.peak_r is None
        ):
            return None
        assessment = assess_adaptive_exit(
            base_tighten_giveback_r=config.profit_defense_tighten_giveback_r,
            base_runner_exit_giveback_r=config.profit_defense_exit_giveback_r,
            recent_ranges=state.recent_ranges,
            risk_per_share=risk,
            evidence=state.latest_exit_evidence,
            config=config,
        )
        state.adaptive_exit_assessment = assessment
        giveback = state.peak_r - current_r
        if (
            state.second_taken
            and not state.profit_defense_runner_exit
            and state.peak_r >= config.profit_defense_exit_activation_r
            and giveback >= assessment.runner_exit_giveback_r
            and current_r > ZERO
            and bearish_close and lower_high
        ):
            return bar.close, state.remaining, "PROFIT_DEFENSE_RUNNER_EXIT"
        if (
            not state.profit_defense_stop_tightened
            and state.peak_r >= config.profit_defense_activation_r
            and giveback >= assessment.tighten_giveback_r
            and current_r > ZERO
            and (bearish_close or lower_than_prior)
        ):
            desired = state.entry_price + (
                state.peak_r - assessment.tighten_giveback_r
            ) * risk
            desired = min(desired, bar.close)
            floor = max(
                state.stop,
                state.entry_price if state.first_taken else state.stop,
            )
            if desired > floor:
                return desired, state.remaining, "PROFIT_DEFENSE_STOP_TIGHTENED"
        return None

    def _open_paper(
        self, signal: MomentumEntrySignal, shares: int, risk_dollars: Decimal,
        float_provenance: FloatProvenance,
        symbol_authorization: PaperSymbolAuthorization,
    ) -> tuple[
        tuple[CaptureRecord, ...], CaptureRecord | None,
        PaperEntryAuthorizationDecision | None,
    ]:
        if signal.symbol in self._paper:
            return (), None, None
        execution_record = None
        authorization_decision = None
        if self._paper_entry_submitter is not None:
            result = self._paper_entry_submitter(signal, shares, risk_dollars)
            if isinstance(result, PaperEntryAuthorizationDecision):
                authorization_decision = result
                try:
                    execution_record = _execution_gate_record(
                        signal, result, symbol_authorization,
                    )
                except Exception:
                    # Diagnostics are deliberately downstream of authorization
                    # and may never alter its result.
                    execution_record = None
                accepted = result.authorized
            else:
                accepted = bool(result)
            if not accepted:
                return (), execution_record, authorization_decision
        first = int((Decimal(shares) * self.config.trade_management.first_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
        second = int((Decimal(shares) * self.config.trade_management.second_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
        state = _PaperState(
            signal, signal.entry_trigger, shares, shares, signal.stop_price,
            first, second, shares, risk_budget=risk_dollars,
            initial_stop=signal.stop_price,
        )
        self._paper[signal.symbol] = state
        protection_records: list[CaptureRecord] = []
        # PAPER placement can synchronously publish a fill before this
        # service has installed its in-memory lifecycle state.  Reconcile
        # immediately after installation so that a nonzero authoritative
        # position can never pass through an implicit unmanaged state.  A
        # later callback remains responsible for fills that arrive after the
        # placement call returns.
        if (
            self._paper_entry_submitter is not None
            and self._paper_position_quantity_source is not None
        ):
            try:
                authoritative_quantity = max(
                    0, int(self._paper_position_quantity_source(signal.symbol))
                )
            except Exception:
                authoritative_quantity = 0
            if authoritative_quantity > 0:
                state.authoritative_position_seen = True
                state.remaining = authoritative_quantity
                if not state.first_taken and not state.second_taken:
                    state.managed_quantity = authoritative_quantity
                    state.first_quantity = int(
                        (
                            Decimal(authoritative_quantity)
                            * self.config.trade_management.first_target_exit_percent
                        ).to_integral_value(rounding=ROUND_FLOOR)
                    )
                    state.second_quantity = int(
                        (
                            Decimal(authoritative_quantity)
                            * self.config.trade_management.second_target_exit_percent
                        ).to_integral_value(rounding=ROUND_FLOOR)
                    )
                try:
                    protection = self._submit_exit(
                        state, state.stop, authoritative_quantity, "STOP",
                    )
                    protection_active = (
                        protection.protection_active
                        if isinstance(protection, PaperExitSubmissionDecision)
                        else bool(protection)
                    )
                except Exception:
                    protection = None
                    protection_active = False
                state.protection_reconciled = protection_active
                if protection_active:
                    state.protective_stop_activated_at = (
                        getattr(protection, "activation_timestamp", None)
                        or signal.timestamp
                    )
                    protection_records.append(CaptureRecord.create(
                        CaptureRecordType.STATE_TRANSITION,
                        signal.symbol, signal.timestamp,
                        {
                            "from": ForwardTransition.PAPER_ENTRY.value,
                            "to": ForwardTransition.PAPER_EXIT_WORKING.value,
                            "reason_codes": ["PROTECTION_REQUIRED"],
                            "authoritative_remaining": authoritative_quantity,
                            "exit_order_id": getattr(protection, "order_id", None),
                        },
                        identity_parts=(
                            ForwardTransition.PAPER_EXIT_WORKING.value,
                            "PROTECTION_REQUIRED",
                            lifecycle_identity(signal),
                        ),
                    ))
                else:
                    protection_records.append(CaptureRecord.create(
                        CaptureRecordType.STATE_TRANSITION,
                        signal.symbol, signal.timestamp,
                        {
                            "from": ForwardTransition.PAPER_ENTRY.value,
                            "to": ForwardTransition.PAPER_EXIT_REQUIRED.value,
                            "reason_codes": ["STOP_PROTECTION_UNAVAILABLE"],
                            "authoritative_remaining": authoritative_quantity,
                        },
                        identity_parts=(
                            ForwardTransition.PAPER_EXIT_REQUIRED.value,
                            "STOP_PROTECTION_UNAVAILABLE",
                            lifecycle_identity(signal),
                        ),
                    ))
                    protection_records.append(self._position_contradiction_record(
                        state, signal.timestamp,
                        reason="PROTECTIVE_EXIT_UNAVAILABLE",
                    ))
        fill = CaptureRecord.create(
            CaptureRecordType.PAPER_FILL, signal.symbol, signal.timestamp,
            {"action": "ENTRY", "entry_authority": "ANALYTICAL_FORWARD_CAPTURE",
             "setup": signal.setup_type.value,
             "lifecycle_id": lifecycle_identity(signal),
             "entry_trigger": signal.entry_trigger, "fill_price": signal.entry_trigger,
             "structural_stop": signal.stop_price, "stop_model": signal.stop_model.value,
             "risk_per_share": signal.risk_per_share, "planned_shares": shares,
             "filled_shares": shares, "risk_dollars": risk_dollars,
             "momentum_score": signal.momentum_score, "spread_percent": signal.spread_percent,
             "relative_volume": signal.relative_volume, "float_shares": signal.float_shares,
             "float_provenance": float_provenance.value,
             "price": signal.reference_price,
             "catalyst_state": signal.catalyst_state.value, "session": signal.session,
             "targets": signal.target_levels, "live_execution_authorized": False,
             "authority": "ANALYTICAL_FORWARD_CAPTURE"},
            identity_parts=("ENTRY",),
        )
        transition = CaptureRecord.create(
            CaptureRecordType.STATE_TRANSITION, signal.symbol, signal.timestamp,
            {"from": ForwardTransition.ENTRY_READY.value,
             "to": ForwardTransition.PAPER_ENTRY.value, "reason_codes": []},
            identity_parts=(ForwardTransition.PAPER_ENTRY.value,),
        )
        self._last_transition[signal.symbol] = ForwardTransition.PAPER_ENTRY
        return (
            (fill, transition, *protection_records,
             _management_context_record(signal.symbol, signal.timestamp, signal, state)),
            execution_record,
            authorization_decision,
        )

    @property
    def open_paper_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._paper))

    @property
    def counterfactual_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._counterfactual))

    def memory_metrics(self) -> dict[str, int]:
        return {"seen_bars_count": len(self._seen_bars),
                "seen_bars_symbols": len({symbol for symbol, _ in self._seen_bars}),
                "last_transition_symbols": len(self._last_transition),
                "paper_symbols": len(self._paper),
                "counterfactual_symbols": len(self._counterfactual)}

    def _advance_paper(self, state: _PaperState, bar: MinuteBar, observed_at) -> tuple[CaptureRecord, ...]:
        if (
            self._paper_entry_submitter is not None
            and self._paper_position_quantity_source is not None
        ):
            return self._advance_authoritative_paper(state, bar, observed_at)
        return self._advance_analytical_paper(state, bar, observed_at)

    def _advance_analytical_paper(
        self, state: _PaperState, bar: MinuteBar, observed_at,
    ) -> tuple[CaptureRecord, ...]:
        state.last_bar_timestamp = bar.timestamp
        self._record_management_range(state, bar)
        signal = state.signal
        records: list[CaptureRecord] = []
        if bar.low <= state.stop:
            state.minimum_low = state.stop if state.minimum_low is None else min(state.minimum_low, state.stop)
            state.maximum_high = (
                state.entry_price if state.maximum_high is None else state.maximum_high
            )
            self._submit_exit(state, state.stop, state.remaining, "STOP")
            records.append(self._paper_fill(state, observed_at, "EXIT", "STOP", state.stop, state.remaining))
            state.realized_pnl += (state.stop - state.entry_price) * state.remaining
            state.remaining = 0
        else:
            state.minimum_low = bar.low if state.minimum_low is None else min(state.minimum_low, bar.low)
            state.maximum_high = bar.high if state.maximum_high is None else max(state.maximum_high, bar.high)
            self._update_peak(state)
            if signal.risk_per_share > ZERO:
                state.current_r = (bar.close - state.entry_price) / signal.risk_per_share
                self._update_peak(state)
            if not state.first_taken and bar.high >= signal.target_levels[0]:
                quantity = min(state.first_quantity, state.remaining)
                self._submit_exit(state, signal.target_levels[0], quantity, "FIRST_TARGET")
                records.append(self._paper_fill(state, observed_at, "PARTIAL", "FIRST_TARGET", signal.target_levels[0], quantity))
                state.realized_pnl += (signal.target_levels[0] - state.entry_price) * quantity
                state.remaining -= quantity
                state.first_taken = True
                state.stop = max(state.stop, state.entry_price)
            if state.remaining and not state.second_taken and bar.high >= signal.target_levels[1]:
                quantity = min(state.second_quantity, state.remaining)
                self._submit_exit(state, signal.target_levels[1], quantity, "SECOND_TARGET")
                records.append(self._paper_fill(state, observed_at, "PARTIAL", "SECOND_TARGET", signal.target_levels[1], quantity))
                state.realized_pnl += (signal.target_levels[1] - state.entry_price) * quantity
                state.remaining -= quantity
                state.second_taken = True
            if state.remaining and bar.high >= signal.target_levels[2]:
                self._submit_exit(state, signal.target_levels[2], state.remaining, "RUNNER_TARGET")
                records.append(self._paper_fill(state, observed_at, "EXIT", "RUNNER_TARGET", signal.target_levels[2], state.remaining))
                state.realized_pnl += (signal.target_levels[2] - state.entry_price) * state.remaining
                state.remaining = 0
            if state.remaining and not records:
                defense = self._profit_defense_action(state, bar)
                if defense is not None:
                    price, quantity, reason = defense
                    if reason == "PROFIT_DEFENSE_STOP_TIGHTENED":
                        state.stop = price
                        state.profit_defense_stop_tightened = True
                        state.profit_defense_last_action = reason
                    else:
                        self._submit_exit(state, price, quantity, reason)
                        records.append(self._paper_fill(
                            state, observed_at, "EXIT", reason, price, quantity,
                        ))
                        state.realized_pnl += (price - state.entry_price) * quantity
                        state.remaining -= quantity
                        state.exit_reason = reason
                        state.exit_price = price
                        state.profit_defense_runner_exit = True
                        state.profit_defense_last_action = reason
                        if state.remaining <= 0:
                            state.remaining = 0
        if state.remaining and state.first_taken and state.prior_low is not None and state.prior_low < bar.close:
            state.stop = max(state.stop, state.prior_low)
        state.prior_low = bar.low
        if not state.remaining:
            risk_dollars = signal.risk_per_share * state.initial_quantity
            realized_r = state.realized_pnl / risk_dollars
            mae_r = (state.minimum_low - state.entry_price) / signal.risk_per_share
            mfe_r = (state.maximum_high - state.entry_price) / signal.risk_per_share
            hold_seconds = Decimal(str((bar.timestamp - signal.timestamp).total_seconds()))
            records.append(CaptureRecord.create(
                CaptureRecordType.STATE_TRANSITION, signal.symbol, observed_at,
                {"from": self._last_transition.get(signal.symbol, ForwardTransition.PAPER_ENTRY).value,
                 "to": ForwardTransition.PAPER_EXIT.value,
                 "reason_codes": [], "realized_r": realized_r,
                 "mae_r": mae_r, "mfe_r": mfe_r,
                 "hold_seconds": hold_seconds},
                identity_parts=(ForwardTransition.PAPER_EXIT.value, bar.timestamp.isoformat()),
            ))
            self._last_transition[signal.symbol] = ForwardTransition.PAPER_EXIT
            self._paper.pop(signal.symbol, None)
        elif records:
            records.append(CaptureRecord.create(
                CaptureRecordType.STATE_TRANSITION, signal.symbol, observed_at,
                {"from": self._last_transition.get(signal.symbol, ForwardTransition.PAPER_ENTRY).value,
                 "to": ForwardTransition.PAPER_PARTIAL.value, "reason_codes": []},
                identity_parts=(ForwardTransition.PAPER_PARTIAL.value, bar.timestamp.isoformat()),
            ))
            self._last_transition[signal.symbol] = ForwardTransition.PAPER_PARTIAL
        # Management context is persisted at bar/state boundaries (not every
        # quote), so a raised stop or high-water mark survives restart.
        records.append(_management_context_record(
            signal.symbol, observed_at, signal, state,
            phase="CLOSED" if not state.remaining else "MANAGING",
        ))
        return tuple(records)

    def _advance_authoritative_paper(
        self, state: _PaperState, bar: MinuteBar, observed_at,
    ) -> tuple[CaptureRecord, ...]:
        """Supervise execution state without inventing fills or flatness."""

        state.last_bar_timestamp = bar.timestamp
        self._record_management_range(state, bar)
        signal = state.signal
        quantity = max(0, int(self._paper_position_quantity_source(signal.symbol)))
        previous = state.remaining if state.authoritative_position_seen else 0
        managed_quantity_before = state.managed_quantity
        # Some embedders expose only the current position projection and do
        # not provide durable order-fill identity.  Preserve a target-fill
        # edge when the projection was already reconciled to the lower value;
        # the production PAPER bridge supplies the stronger identity source.
        if (
            self._paper_exit_fill_source is None
            and previous <= quantity
            and (
                quantity < max(state.managed_quantity, managed_quantity_before)
                or quantity == state.first_quantity
            )
            and state.active_exit_role in {"FIRST_TARGET", "SECOND_TARGET"}
            and not state.first_taken
        ):
            previous = max(state.managed_quantity, managed_quantity_before)
        records: list[CaptureRecord] = []

        if quantity <= 0:
            if not state.authoritative_position_seen:
                # The entry order is still working (or was cancelled).  The
                # gateway owns entry invalidation and no position management
                # may be fabricated before an authoritative fill.
                if (
                    self._paper_execution_ownership_source is None
                    or self._paper_execution_ownership_source(signal.symbol)
                ):
                    return ()
                records.append(CaptureRecord.create(
                    CaptureRecordType.STATE_TRANSITION, signal.symbol, observed_at,
                    {"from": self._last_transition.get(signal.symbol, ForwardTransition.PAPER_ENTRY).value,
                     "to": ForwardTransition.ENTRY_BLOCKED.value,
                     "reason_codes": ["ENTRY_TERMINATED_WITHOUT_POSITION"],
                     "authoritative_remaining": 0},
                    identity_parts=("ENTRY_TERMINATED_WITHOUT_POSITION",
                                    bar.timestamp.isoformat()),
                ))
                records.append(_management_context_record(
                    signal.symbol, observed_at, signal, state,
                    phase="ENTRY_CANCELLED",
                ))
                self._last_transition[signal.symbol] = ForwardTransition.ENTRY_BLOCKED
                self._paper.pop(signal.symbol, None)
                return tuple(records)
            state.remaining = 0
            exit_role, exit_order_id = self._observed_exit_role(state)
            final_fill_price = self._latest_exit_fill_price(
                state, exit_role,
            )
            if final_fill_price is not None and previous > 0:
                state.realized_pnl += (
                    final_fill_price - state.entry_price
                ) * Decimal(previous)
            self._record_exit_fill_classification(
                state, exit_role, exit_order_id, previous, 0, observed_at,
            )
            if exit_role in {"FIRST_TARGET", "SECOND_TARGET", "RUNNER_EXIT"}:
                self._record_management_event(
                    state, "PROFIT_TARGET_FILLED", timestamp=observed_at,
                    target_stage=exit_role,
                )
            elif exit_role == "PROTECTIVE_STOP":
                # A stop is terminal protection, never a target completion.
                state.exit_reason = None
                state.exit_price = None
            self._record_management_event(
                state, "STOP_CANCELLED_BY_POSITION_CLOSE", timestamp=observed_at,
            )
            records.append(self._authoritative_exit_record(state, bar, observed_at))
            records.append(_management_context_record(
                signal.symbol, observed_at, signal, state, phase="CLOSED",
            ))
            self._last_transition[signal.symbol] = ForwardTransition.PAPER_EXIT
            self._paper.pop(signal.symbol, None)
            return tuple(records)

        state.authoritative_position_seen = True
        # Entry planning quantity is not a management authority.  Once the
        # position projection proves the actual filled basis, derive the
        # milestones from that quantity and leave the runner as the exact
        # remainder.  After a milestone is complete, its quantities are
        # immutable so restart/late position observations cannot rewrite
        # completed target state.
        if (
            not state.first_taken
            and not state.second_taken
            and state.exit_reason is None
            and state.managed_quantity != quantity
        ):
            state.managed_quantity = quantity
            state.first_quantity = int((Decimal(quantity) * self.config.trade_management.first_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
            state.second_quantity = int((Decimal(quantity) * self.config.trade_management.second_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
        state.remaining = quantity
        if (
            self._paper_exit_fill_source is None
            and state.active_exit_role == "FIRST_TARGET"
            and not state.first_taken
            and quantity == state.first_quantity
            and max(state.managed_quantity, state.initial_quantity) > quantity
        ):
            # Legacy test/adaptor ports expose only the already-reconciled
            # quantity.  A half-size remainder with an active first-target
            # identity is the unambiguous target partial-fill edge.
            state.first_taken = True
            state.stop = max(state.stop, state.entry_price)
            state.exit_reason = None
            state.exit_price = None
            self._record_management_event(
                state, "PROFIT_TARGET_PARTIAL_FILL", timestamp=observed_at,
                target_stage="FIRST_TARGET",
            )
        state.minimum_low = bar.low if state.minimum_low is None else min(state.minimum_low, bar.low)
        state.maximum_high = bar.high if state.maximum_high is None else max(state.maximum_high, bar.high)
        self._update_peak(state)
        if signal.risk_per_share > ZERO:
            state.current_r = (bar.close - state.entry_price) / signal.risk_per_share
            self._update_peak(state)

        # A fill creates exposure immediately.  Protection is therefore an
        # invariant of the first authoritative position observation, not a
        # consequence of a later bar touching the structural stop.  In
        # particular, never let target evaluation run while a newly filled
        # position is unprotected.
        protection_active = (
            state.protective_stop_activated_at is not None
            and state.protection_reconciled
        )
        protection_activated_this_bar = False
        if not protection_active:
            result = self._submit_exit(state, state.stop, quantity, "STOP")
            self._capture_stop_activation(state, result, bar)
            # This is the first bar for which this service has authoritative
            # protection ownership.  Even if a recovered gateway reports a
            # coarse creation timestamp equal to the bar open, OHLC cannot
            # prove the low happened after activation, so this bar remains
            # ambiguous.
            protection_activated_this_bar = True
            protection_active = (
                result.protection_active
                if isinstance(result, PaperExitSubmissionDecision)
                else bool(result)
            )
            state.protection_reconciled = protection_active
            if protection_active:
                self._record_management_event(
                    state, "PROTECTIVE_STOP_RECONCILED", timestamp=observed_at,
                )
            if not protection_active:
                records.append(CaptureRecord.create(
                    CaptureRecordType.STATE_TRANSITION, signal.symbol, observed_at,
                    {"from": self._last_transition.get(signal.symbol, ForwardTransition.PAPER_ENTRY).value,
                     "to": ForwardTransition.PAPER_EXIT_REQUIRED.value,
                     "reason_codes": ["STOP_PROTECTION_UNAVAILABLE"],
                     "authoritative_remaining": quantity},
                    identity_parts=(ForwardTransition.PAPER_EXIT_REQUIRED.value,
                                    "STOP_PROTECTION_UNAVAILABLE",
                                    bar.timestamp.isoformat()),
                ))
                records.append(self._position_contradiction_record(
                    state, observed_at, reason="PROTECTIVE_EXIT_UNAVAILABLE",
                ))
                state.prior_low = bar.low
                records.append(_management_context_record(
                    signal.symbol, observed_at, signal, state, phase="MANAGING",
                ))
                return tuple(records)

        # Once authoritative exposure exists, stage the first passive profit
        # leg immediately alongside protection. Previously this was delayed
        # until a later bar crossed the target, leaving a profitable position
        # with only its protective stop working.
        target_staged_this_bar = False
        target_attempted_this_bar = False
        if (
            not state.first_taken
            and state.first_quantity > 0
            and state.exit_reason is None
            and protection_active
            and (
                signal.structural_stop_price is None
                or bar.close > signal.structural_stop_price
            )
        ):
            first_price = signal.target_levels[0]
            target_attempted_this_bar = True
            staged = self._submit_exit(
                state, first_price,
                min(state.first_quantity, quantity), "FIRST_TARGET",
                target_stage_only=True,
            )
            staged_state = (
                staged.state
                if isinstance(staged, PaperExitSubmissionDecision)
                else None
            )
            if staged_state in {
                PaperExitSubmissionState.SUBMITTED,
                PaperExitSubmissionState.WORKING,
            }:
                state.exit_reason = "FIRST_TARGET"
                state.exit_price = first_price
                target_staged_this_bar = True
                if (
                    state.peak_r is not None
                    and state.peak_r
                    >= self.config.trade_management.move_stop_to_breakeven_after_r
                ):
                    state.stop = max(state.stop, state.entry_price)
                self._record_management_event(
                    state, "PROFIT_TARGET_STAGED", timestamp=observed_at,
                    target_stage="FIRST_TARGET", target_price=first_price,
                )

        if self._last_transition.get(signal.symbol) is ForwardTransition.PAPER_EXIT:
            records.append(self._position_contradiction_record(state, observed_at))

        # A lightweight PAPER test/adapter can reconcile the position before
        # this bar reaches the service.  The durable target role plus an exact
        # first-target-sized remainder is still sufficient to attribute that
        # reduction without relying on the mutable reason alone.
        if (
            self._paper_exit_fill_source is None
            and state.exit_reason == "FIRST_TARGET"
            and not state.first_taken
            and quantity == state.first_quantity
            and state.managed_quantity >= quantity * 2
        ):
            state.first_taken = True
            state.stop = max(state.stop, state.entry_price)
            self._record_management_event(
                state, "PROFIT_TARGET_PARTIAL_FILL", timestamp=observed_at,
                target_stage="FIRST_TARGET",
            )

        if previous > quantity:
            exit_role, exit_order_id = self._observed_exit_role(state)
            fill_quantity = max(0, previous - quantity)
            first_target_complete = (
                exit_role == "FIRST_TARGET"
                and self._paper_exit_order_complete(
                    state, exit_order_id, "FIRST_TARGET", fill_quantity,
                )
            )
            self._record_exit_fill_classification(
                state, exit_role, exit_order_id, previous, quantity, observed_at,
            )
            if exit_role == "FIRST_TARGET":
                if first_target_complete:
                    state.first_taken = True
                    state.stop = max(state.stop, state.entry_price)
                    self._record_management_event(
                        state, "PROFIT_TARGET_FILLED", timestamp=observed_at,
                        target_stage="FIRST_TARGET",
                    )
                else:
                    # A partial first-target fill leaves the first target
                    # stage open for its residual allocation.  SECOND_TARGET
                    # is not eligible until durable completion.
                    state.first_taken = False
                    state.exit_reason = "FIRST_TARGET"
                    state.exit_price = signal.target_levels[0]
                    self._record_management_event(
                        state, "PROFIT_TARGET_PARTIAL_FILL", timestamp=observed_at,
                        target_stage="FIRST_TARGET",
                    )
            elif exit_role == "SECOND_TARGET":
                state.second_taken = True
                self._record_management_event(
                    state, "PROFIT_TARGET_PARTIAL_FILL", timestamp=observed_at,
                    target_stage="SECOND_TARGET",
                )
            elif exit_role and exit_role.startswith("PROFIT_HARVEST_"):
                fill_price = self._latest_exit_fill_price(state, exit_role)
                if fill_price is not None:
                    realized = (
                        fill_price - state.entry_price
                    ) * Decimal(fill_quantity)
                    state.realized_pnl += realized
                    state.realized_from_partials += realized
                try:
                    state.profit_harvest_stage = max(
                        state.profit_harvest_stage,
                        int(exit_role.rsplit("_", 1)[-1]),
                    )
                except ValueError:
                    pass
                state.pending_profit_harvest_role = None
                self._record_management_event(
                    state, "PROFIT_HARVEST_FILLED", timestamp=observed_at,
                    target_stage=exit_role, fill_quantity=fill_quantity,
                    order_id=exit_order_id,
                )
            elif exit_role == "PROTECTIVE_STOP":
                # Do not let a stale persisted FIRST_TARGET label relabel a
                # protective-stop fill.  The target stage remains incomplete.
                state.exit_reason = None
                state.exit_price = None
            if not (exit_role == "FIRST_TARGET" and not first_target_complete):
                state.exit_reason = None
                state.exit_price = None
            records.append(CaptureRecord.create(
                CaptureRecordType.STATE_TRANSITION, signal.symbol, observed_at,
                {"from": self._last_transition.get(signal.symbol, ForwardTransition.PAPER_ENTRY).value,
                 "to": ForwardTransition.PAPER_PARTIAL.value,
                 "reason_codes": [], "authoritative_remaining": quantity},
                identity_parts=(ForwardTransition.PAPER_PARTIAL.value,
                                bar.timestamp.isoformat(), str(quantity)),
            ))
            self._last_transition[signal.symbol] = ForwardTransition.PAPER_PARTIAL

            if state.first_taken and quantity > 0:
                self._record_management_event(
                    state, "RUNNER_ACTIVE", timestamp=observed_at,
                    runner_quantity=quantity,
                )

            # Move directly to the next configured milestone after an
            # authoritative first-target reduction; do not wait for another
            # bar to cross target two.
            if (
                state.first_taken
                and not state.second_taken
                and state.second_quantity > 0
                and quantity > 0
            ):
                second_price = signal.target_levels[1]
                staged = self._submit_exit(
                    state, second_price,
                    min(state.second_quantity, quantity), "SECOND_TARGET",
                )
                staged_state = (
                    staged.state
                    if isinstance(staged, PaperExitSubmissionDecision)
                    else None
                )
                if staged_state in {
                    PaperExitSubmissionState.SUBMITTED,
                    PaperExitSubmissionState.WORKING,
                }:
                    state.exit_reason = "SECOND_TARGET"
                    state.exit_price = second_price
                    self._record_management_event(
                        state, "PROFIT_TARGET_STAGED", timestamp=observed_at,
                        target_stage="SECOND_TARGET", target_price=second_price,
                    )
                    if quantity > state.second_quantity:
                        self._record_management_event(
                            state, "RUNNER_ACTIVE", timestamp=observed_at,
                            runner_quantity=quantity - state.second_quantity,
                )

        if (
            state.first_taken
            and not state.second_taken
            and state.second_quantity > 0
            and quantity > 0
            and bar.high >= signal.target_levels[1]
            and state.exit_reason is None
        ):
            second_price = signal.target_levels[1]
            staged = self._submit_exit(
                state, second_price, min(state.second_quantity, quantity),
                "SECOND_TARGET",
            )
            staged_state = (
                staged.state
                if isinstance(staged, PaperExitSubmissionDecision) else None
            )
            if staged_state in {
                PaperExitSubmissionState.SUBMITTED,
                PaperExitSubmissionState.WORKING,
            }:
                state.exit_reason = "SECOND_TARGET"
                state.exit_price = second_price
                self._record_management_event(
                    state, "PROFIT_TARGET_STAGED", timestamp=observed_at,
                    target_stage="SECOND_TARGET", target_price=second_price,
                )

        # The target is staged immediately after entry, but the stop promotion
        # still occurs only when price actually reaches the first milestone.
        if (
            not state.first_taken
            and state.exit_reason == "FIRST_TARGET"
            and bar.high >= signal.target_levels[0]
            and state.peak_r is not None
            and state.peak_r >= self.config.trade_management.move_stop_to_breakeven_after_r
        ):
            state.stop = max(state.stop, state.entry_price)
            self._submit_exit(state, state.stop, quantity, "STOP")
            # The target remains the active fill identity; the stop call only
            # amends correlated protection.
            state.active_exit_role = "FIRST_TARGET"
            state.exit_reason = "FIRST_TARGET"

        requested: tuple[Decimal, int, str] | None = None
        stop_breach = bar.low <= state.stop
        stop_eligible = stop_breach and (
            not protection_activated_this_bar
            and not target_staged_this_bar
            and
            state.protective_stop_activated_at is not None
            and state.protective_stop_activated_at <= bar.timestamp
            # An already-working target owns this bar's upside/downside
            # ambiguity.  Do not let a single OHLC low overwrite the target
            # lifecycle when the same bar also reaches its target level.
            and not (
                state.exit_reason in {"FIRST_TARGET", "SECOND_TARGET"}
                and state.exit_price is not None
                and bar.high >= state.exit_price
                and self._paper_target_is_working(state)
            )
        )
        if stop_breach and not stop_eligible:
            # Establish protection, but do not infer that an OHLC extreme
            # occurred after a stop that became active inside this bar.
            result = self._submit_exit(state, state.stop, quantity, "STOP")
            self._capture_stop_activation(state, result, bar)
            active = (
                result.protection_active
                if isinstance(result, PaperExitSubmissionDecision)
                else bool(result)
            )
            if not active:
                records.append(CaptureRecord.create(
                    CaptureRecordType.STATE_TRANSITION, signal.symbol, observed_at,
                    {"from": self._last_transition.get(signal.symbol, ForwardTransition.PAPER_ENTRY).value,
                     "to": ForwardTransition.PAPER_EXIT_REQUIRED.value,
                     "reason_codes": ["STOP_PROTECTION_UNAVAILABLE"],
                     "authoritative_remaining": quantity},
                    identity_parts=(ForwardTransition.PAPER_EXIT_REQUIRED.value,
                                    "STOP_PROTECTION_UNAVAILABLE",
                                    bar.timestamp.isoformat()),
                ))
                records.append(self._position_contradiction_record(
                    state, observed_at, reason="PROTECTIVE_EXIT_UNAVAILABLE",
                ))
                state.prior_low = bar.low
                records.append(_management_context_record(
                    signal.symbol, observed_at, signal, state, phase="MANAGING",
                ))
                return tuple(records)
        if stop_eligible:
            requested = (state.stop, quantity, "STOP")
        elif (
            state.exit_reason is not None
            and state.exit_price is not None
            and state.exit_reason not in {"FIRST_TARGET", "SECOND_TARGET"}
            and not target_staged_this_bar
        ):
            # A working partial target remains reserved at its original
            # milestone size until the authoritative position decreases.
            # Retrying it with the full remaining position would destroy the
            # correlated target/stop bracket on every later management bar.
            pending_quantity = quantity
            if state.exit_reason == "FIRST_TARGET":
                pending_quantity = min(state.first_quantity, quantity)
            elif state.exit_reason == "SECOND_TARGET":
                pending_quantity = min(state.second_quantity, quantity)
            requested = (
                state.exit_price, pending_quantity, state.exit_reason,
            )
        elif (not target_staged_this_bar
              and not target_attempted_this_bar
              and not state.first_taken
              and bar.high >= signal.target_levels[0]):
            requested = (signal.target_levels[0], min(state.first_quantity, quantity), "FIRST_TARGET")
        elif (not target_staged_this_bar
              and not target_attempted_this_bar
              and state.first_taken
              and not state.second_taken
              and bar.high >= signal.target_levels[1]):
            requested = (signal.target_levels[1], min(state.second_quantity, quantity), "SECOND_TARGET")
        elif (not target_staged_this_bar
              and not target_attempted_this_bar
              and state.second_taken
              and bar.high >= signal.target_levels[2]):
            requested = (signal.target_levels[2], quantity, "RUNNER_TARGET")
        # The first protection-reconciliation bar is ambiguous.
        # Do not bypass that safeguard through profit-defense exits.
        if requested is None and not protection_activated_this_bar:
            requested = self._profit_defense_action(state, bar)

        if requested is not None:
            price, requested_quantity, reason = requested
            submission_reason = (
                "STOP"
                if reason == "PROFIT_DEFENSE_STOP_TIGHTENED"
                else reason
            )
            result = self._submit_exit(
                state, price, requested_quantity, submission_reason,
            )
            if (isinstance(result, PaperExitSubmissionDecision)
                    and result.state is PaperExitSubmissionState.COMPLETED):
                if reason == "FIRST_TARGET":
                    state.first_taken = True
                elif reason == "SECOND_TARGET":
                    state.second_taken = True
                if reason in {"FIRST_TARGET", "SECOND_TARGET", "RUNNER_TARGET"}:
                    self._record_management_event(
                        state, "PROFIT_TARGET_FILLED", timestamp=observed_at,
                        target_stage=reason,
                    )
                state.exit_reason = None
                state.exit_price = None
                state.prior_low = bar.low
                records.append(_management_context_record(
                    signal.symbol, observed_at, signal, state, phase="MANAGING"))
                return tuple(records)
            if reason == "STOP":
                self._capture_stop_activation(state, result, bar)
            active = (
                result.protection_active
                if isinstance(result, PaperExitSubmissionDecision)
                else bool(result)
            )
            if active and reason != "PROFIT_DEFENSE_STOP_TIGHTENED":
                # Pending means the submission boundary proved a real order,
                # not merely that management intended to submit one.
                state.exit_reason = reason
                state.exit_price = price
            elif (
                not active
                and reason in {"FIRST_TARGET", "SECOND_TARGET"}
                and not self._paper_target_is_working(state)
            ):
                # A pre-gateway/non-durable failure remains retryable without
                # masquerading as a working target.
                if state.exit_reason == reason:
                    state.exit_reason = None
                    state.exit_price = None
                if state.active_exit_role == reason:
                    state.active_exit_role = None
                    state.active_exit_order_id = None
            transition = (
                ForwardTransition.PAPER_EXIT_WORKING
                if active else ForwardTransition.PAPER_EXIT_REQUIRED
            )
            if self._last_transition.get(signal.symbol) is not transition:
                records.append(CaptureRecord.create(
                    CaptureRecordType.STATE_TRANSITION, signal.symbol, observed_at,
                    {"from": self._last_transition.get(signal.symbol, ForwardTransition.PAPER_ENTRY).value,
                     "to": transition.value, "reason_codes": [reason],
                     "authoritative_remaining": quantity,
                     "exit_order_id": getattr(result, "order_id", None)},
                    identity_parts=(transition.value, reason,
                                    bar.timestamp.isoformat()),
                ))
                self._last_transition[signal.symbol] = transition
            if not active:
                records.append(self._position_contradiction_record(
                    state, observed_at, reason="PROTECTIVE_EXIT_UNAVAILABLE",
                ))
            elif reason == "PROFIT_DEFENSE_STOP_TIGHTENED":
                state.stop = max(state.stop, price)
                state.profit_defense_stop_tightened = True
                state.profit_defense_last_action = reason
                self._record_management_event(
                    state, "PROFIT_DEFENSE_PARTIAL_EXIT", timestamp=observed_at,
                )
            elif (
                reason == "FIRST_TARGET"
                and state.peak_r is not None
                and state.peak_r
                >= self.config.trade_management.move_stop_to_breakeven_after_r
            ):
                # A working passive target is not a realized partial.  Still
                # retain the configured break-even floor after the market has
                # proved +1R so a subsequent STOP replacement cannot restore
                # the original-loss stop while cancelling that target.
                state.stop = max(state.stop, state.entry_price)
            elif reason == "PROFIT_DEFENSE_RUNNER_EXIT":
                state.profit_defense_runner_exit = True
                state.profit_defense_last_action = reason
                self._record_management_event(
                    state, "PROFIT_DEFENSE_RUNNER_EXIT", timestamp=observed_at,
                )

            # A failed target retry must not monopolize the management cycle.
            # Independently protect the same authoritative exposure while the
            # target remains eligible for a bounded later retry.
            if (
                not active
                and reason in {"FIRST_TARGET", "SECOND_TARGET"}
                and not protection_activated_this_bar
            ):
                defense = self._profit_defense_action(state, bar)
                if defense is not None:
                    defense_price, defense_quantity, defense_reason = defense
                    defense_submission_reason = (
                        "STOP"
                        if defense_reason == "PROFIT_DEFENSE_STOP_TIGHTENED"
                        else defense_reason
                    )
                    defense_result = self._submit_exit(
                        state,
                        defense_price,
                        defense_quantity,
                        defense_submission_reason,
                    )
                    defense_active = (
                        defense_result.protection_active
                        if isinstance(
                            defense_result, PaperExitSubmissionDecision
                        )
                        else bool(defense_result)
                    )
                    if (
                        defense_active
                        and defense_reason
                        == "PROFIT_DEFENSE_STOP_TIGHTENED"
                    ):
                        state.stop = max(state.stop, defense_price)
                        state.profit_defense_stop_tightened = True
                        state.profit_defense_last_action = defense_reason
                        self._record_management_event(
                            state,
                            "PROFIT_DEFENSE_PARTIAL_EXIT",
                            timestamp=observed_at,
                        )
                    elif defense_active:
                        state.exit_reason = defense_reason
                        state.exit_price = defense_price

        if (
            state.first_taken
            and state.exit_reason != "RUNNER_TARGET"
            and state.prior_low is not None
            and state.prior_low < bar.close
        ):
            # ``state.stop`` is a projection of canonical protection, not a
            # substitute for it. Raising only the projection can make later
            # profit-defense evaluation believe the tighter stop is already
            # working while the gateway still owns the original-loss stop.
            trailing_stop = max(state.stop, state.prior_low)
            if trailing_stop > state.stop:
                target_role = state.active_exit_role
                target_order_id = state.active_exit_order_id
                target_reason = state.exit_reason
                target_price = state.exit_price
                result = self._submit_exit(
                    state, trailing_stop, quantity, "STOP",
                )
                active = (
                    result.protection_active
                    if isinstance(result, PaperExitSubmissionDecision)
                    else bool(result)
                )
                if active:
                    state.stop = trailing_stop
                # A stop amendment must not steal durable fill attribution
                # from a concurrently working target.
                if target_reason in {"FIRST_TARGET", "SECOND_TARGET"}:
                    state.active_exit_role = target_role
                    state.active_exit_order_id = target_order_id
                    state.exit_reason = target_reason
                    state.exit_price = target_price
        state.prior_low = bar.low
        records.append(_management_context_record(
            signal.symbol, observed_at, signal, state,
            phase=("EXIT_WORKING" if state.exit_reason is not None else "MANAGING"),
        ))
        return tuple(records)

    @staticmethod
    def _capture_stop_activation(state: _PaperState, result: object, bar: MinuteBar) -> None:
        active = (
            result.protection_active
            if isinstance(result, PaperExitSubmissionDecision)
            else bool(result)
        )
        if not active:
            return
        activation = getattr(result, "activation_timestamp", None)
        if activation is None:
            # A legacy boolean submitter cannot provide order timing.  Treat
            # protection as active at bar close; the next complete bar is the
            # first bar whose OHLC can prove a breach.
            activation = bar.timestamp + timedelta(minutes=1)
        if state.protective_stop_activated_at is None or activation > state.protective_stop_activated_at:
            state.protective_stop_activated_at = activation

    def _authoritative_exit_record(
        self, state: _PaperState, bar: MinuteBar, observed_at,
    ) -> CaptureRecord:
        return CaptureRecord.create(
            CaptureRecordType.STATE_TRANSITION, state.signal.symbol, observed_at,
            {"from": self._last_transition.get(state.signal.symbol, ForwardTransition.PAPER_ENTRY).value,
             "to": ForwardTransition.PAPER_EXIT.value, "reason_codes": [],
             "authoritative_remaining": 0,
             "authority": "AUTHORITATIVE_POSITION_PROJECTION",
             "peak_executable_bid": state.peak_executable_bid,
             "peak_executable_pnl": state.peak_executable_pnl,
             "peak_executable_r": state.peak_executable_r,
             "realized_lifecycle_profit": state.realized_pnl,
             "realized_from_partials": state.realized_from_partials,
             "peak_to_exit_giveback": max(
                 ZERO, state.peak_executable_pnl - state.realized_pnl,
             ),
             "peak_profit_retention_percent": (
                 None
                 if state.peak_executable_pnl <= ZERO
                 else state.realized_pnl / state.peak_executable_pnl * HUNDRED
             )},
            identity_parts=(ForwardTransition.PAPER_EXIT.value,
                            bar.timestamp.isoformat(), "AUTHORITATIVE"),
        )

    def _position_contradiction_record(
        self, state: _PaperState, observed_at, *, reason: str = "ANALYTICAL_CLOSED_AUTHORITATIVE_OPEN",
    ) -> CaptureRecord:
        return CaptureRecord.create(
            CaptureRecordType.STATE_TRANSITION, state.signal.symbol, observed_at,
            {"from": self._last_transition.get(state.signal.symbol, ForwardTransition.PAPER_ENTRY).value,
             "to": ForwardTransition.PAPER_POSITION_CONTRADICTION.value,
             "reason_codes": [reason], "severity": "CRITICAL",
             "authoritative_remaining": state.remaining,
             "new_same_symbol_execution": "FAIL_CLOSED"},
            identity_parts=(ForwardTransition.PAPER_POSITION_CONTRADICTION.value,
                            reason, str(state.last_bar_timestamp)),
        )

    def _submit_exit(
        self, state: _PaperState, price: Decimal, quantity: int, reason: str,
        *, target_stage_only: bool = False,
    ) -> object:
        if self._paper_exit_submitter is not None:
            try:
                result = self._paper_exit_submitter(
                    state.signal.symbol, quantity, price, reason,
                    lifecycle_identity(state.signal),
                    target_stage_only=target_stage_only,
                )
            except TypeError:
                result = self._paper_exit_submitter(
                    state.signal.symbol, quantity, price, reason,
                    lifecycle_identity(state.signal),
                )
            role = self._exit_role_for_reason(reason)
            if role is not None and (
                not isinstance(result, PaperExitSubmissionDecision)
                or result.state in {
                    PaperExitSubmissionState.SUBMITTED,
                    PaperExitSubmissionState.WORKING,
                    PaperExitSubmissionState.COMPLETED,
                }
            ):
                state.active_exit_role = role
                state.active_exit_order_id = getattr(result, "order_id", None)
            return result
        return False

    @staticmethod
    def _exit_role_for_reason(reason: str | None) -> str | None:
        key = str(reason or "").strip().upper()
        return {
            "STOP": "PROTECTIVE_STOP", "STOP_LOSS": "PROTECTIVE_STOP",
            "FIRST_TARGET": "FIRST_TARGET", "SECOND_TARGET": "SECOND_TARGET",
            "RUNNER_TARGET": "RUNNER_EXIT",
            "PROFIT_DEFENSE_RUNNER_EXIT": "PROFIT_DEFENSE",
            "PROFIT_DEFENSE_STOP_TIGHTENED": "PROFIT_DEFENSE",
            "PROFIT_HARVEST_1": "PROFIT_HARVEST_1",
            "PROFIT_HARVEST_2": "PROFIT_HARVEST_2",
            "PROFIT_HARVEST_3": "PROFIT_HARVEST_3",
        }.get(key)

    def _observed_exit_role(
        self, state: _PaperState,
    ) -> tuple[str | None, str | None]:
        """Use actual PAPER order identity when a position reduction is seen."""
        if self._paper_exit_fill_source is not None:
            try:
                role, order_id = self._paper_exit_fill_source(
                    state.signal.symbol, lifecycle_identity(state.signal),
                )
                if role:
                    return str(role).strip().upper(), order_id
            except Exception:
                pass
        return state.active_exit_role or self._exit_role_for_reason(state.exit_reason), state.active_exit_order_id

    def _record_exit_fill_classification(
        self, state: _PaperState, role: str | None, order_id: str | None,
        before: int, after: int, timestamp: datetime,
    ) -> None:
        self._record_management_event(
            state, "EXIT_FILL_CLASSIFIED", timestamp=timestamp,
            order_role=role or "UNKNOWN",
            order_id=(str(order_id)[-32:] if order_id else None),
            fill_quantity=max(0, int(before) - int(after)),
            authoritative_qty_before=int(before),
            authoritative_qty_after=int(after),
        )

    def manage_session_boundary(self, observed_at: datetime) -> tuple[tuple[str, str], ...]:
        """Flatten or explicitly approve carry for each authoritative PAPER position."""
        config = self.config.session_management
        if not config.enabled or not flatten_window_reached(observed_at, config):
            return ()
        outcomes: list[tuple[str, str]] = []
        records: list[CaptureRecord] = []
        for symbol, state in tuple(self._paper.items()):
            quantity = max(0, state.remaining)
            if self._paper_position_quantity_source is not None:
                quantity = max(0, int(self._paper_position_quantity_source(symbol)))
            if quantity <= 0:
                continue
            evidence = state.latest_exit_evidence
            adaptive = state.adaptive_exit_assessment
            assessment = assess_overnight_carry(
                config=config,
                protection_active=state.protection_reconciled,
                current_r=state.current_r,
                peak_r=state.peak_r,
                giveback_r=state.giveback_r,
                quote_fresh=bool(evidence is not None and evidence.quote_fresh),
                pressure_score=None if adaptive is None else adaptive.pressure_score,
                overnight_available=overnight_session_follows(observed_at),
            )
            if assessment.carry:
                outcomes.append((symbol, "OVERNIGHT_CARRY_APPROVED"))
                records.append(_management_context_record(
                    symbol, observed_at, state.signal, state, phase="OVERNIGHT_CARRY_APPROVED",
                ))
                continue
            price = state.entry_price
            if evidence is not None and evidence.best_bid is not None:
                price = evidence.best_bid
            result = self._submit_exit(state, price, quantity, "SESSION_CLOSE")
            submitted = (
                result.state in {PaperExitSubmissionState.SUBMITTED, PaperExitSubmissionState.WORKING}
                if isinstance(result, PaperExitSubmissionDecision) else bool(result)
            )
            outcome = "SESSION_CLOSE_SUBMITTED" if submitted else "SESSION_CLOSE_UNAVAILABLE"
            outcomes.append((symbol, outcome))
            records.append(_management_context_record(
                symbol, observed_at, state.signal, state, phase=outcome,
            ))
        if records:
            self._submit_records(tuple(records))
        return tuple(outcomes)

    def flatten_for_overnight_capability_loss(
        self, observed_at: datetime,
    ) -> tuple[tuple[str, str], ...]:
        """Submit exits when the new overnight session denies entitlement."""
        outcomes: list[tuple[str, str]] = []
        for symbol, state in tuple(self._paper.items()):
            quantity = max(0, state.remaining)
            if self._paper_position_quantity_source is not None:
                quantity = max(0, int(self._paper_position_quantity_source(symbol)))
            if quantity <= 0:
                continue
            evidence = state.latest_exit_evidence
            price = (
                evidence.best_bid
                if evidence is not None and evidence.best_bid is not None
                else state.entry_price
            )
            result = self._submit_exit(
                state, price, quantity, "OVERNIGHT_CAPABILITY_LOST",
            )
            submitted = (
                result.state in {PaperExitSubmissionState.SUBMITTED, PaperExitSubmissionState.WORKING}
                if isinstance(result, PaperExitSubmissionDecision) else bool(result)
            )
            outcomes.append((
                symbol,
                "OVERNIGHT_CAPABILITY_EXIT_SUBMITTED"
                if submitted else "OVERNIGHT_CAPABILITY_EXIT_UNAVAILABLE",
            ))
        return tuple(outcomes)

    def reconcile_authoritative_protection(
        self, symbol: str, observed_at: datetime,
    ) -> bool:
        """Prove every required PAPER bracket leg in the durable order ledger."""
        state = self._paper.get(symbol.strip().upper())
        if state is None or self._paper_position_quantity_source is None:
            return False
        quantity = max(0, int(self._paper_position_quantity_source(symbol)))
        if quantity <= 0:
            return False
        self._record_management_event(
            state, "BRACKET_RECONCILIATION_STARTED", timestamp=observed_at,
        )
        # Preserve a decrease until bar management acknowledges the target fill.
        # Overwriting remaining here erased the only completion evidence and
        # caused FIRST_TARGET to be issued repeatedly down to a one-share runner.
        first_authoritative_position = not state.authoritative_position_seen
        if first_authoritative_position or quantity > state.remaining:
            state.remaining = quantity
        if first_authoritative_position:
            # Reconciliation can be the first callback to observe a partial
            # entry fill. Bind milestones to actual filled exposure before
            # marking the lifecycle authoritative; otherwise a later tick
            # preserves planned order quantity in peak-PnL/harvest sizing.
            state.managed_quantity = quantity
            if not state.first_taken and not state.second_taken:
                state.first_quantity = int((
                    Decimal(quantity)
                    * self.config.trade_management.first_target_exit_percent
                ).to_integral_value(rounding=ROUND_FLOOR))
                state.second_quantity = int((
                    Decimal(quantity)
                    * self.config.trade_management.second_target_exit_percent
                ).to_integral_value(rounding=ROUND_FLOOR))
        state.authoritative_position_seen = True
        desired_target_quantity = 0
        desired_target_price: Decimal | None = None
        target_role: str | None = None

        def incomplete(reason: str, **details: object) -> bool:
            self._record_management_event(
                state,
                "BRACKET_RECONCILIATION_INCOMPLETE",
                timestamp=observed_at,
                reason=reason,
                target_stage=target_role,
                desired_target_qty=desired_target_quantity,
                desired_target_price=desired_target_price,
                **details,
            )
            return False

        # Without the canonical ledger no submission result can prove that a
        # bracket leg became durable.  Fail closed before creating an
        # unverifiable duplicate on every reconciliation pass.
        if self._paper_order_book() is None:
            state.protection_reconciled = False
            return incomplete("TARGET_NOT_DURABLE")

        stop_leg = self._durable_exit_leg(
            state, "STOP", desired_quantity=quantity, desired_price=state.stop,
        )
        stop_result: object | None = None
        if not stop_leg.is_exact:
            stop_result = self._submit_exit(state, state.stop, quantity, "STOP")
            stop_leg = self._durable_exit_leg(
                state, "STOP", desired_quantity=quantity,
                desired_price=state.stop,
            )
        if not stop_leg.is_exact:
            state.protection_reconciled = False
            return incomplete("STOP_NOT_DURABLE")

        activation = (
            getattr(stop_leg.matching_order, "updated_at", None)
            or getattr(stop_result, "activation_timestamp", None)
            or observed_at
        )
        if (
            state.protective_stop_activated_at is None
            or activation > state.protective_stop_activated_at
        ):
            state.protective_stop_activated_at = activation
        state.protection_reconciled = True
        self._record_management_event(
            state, "PROTECTIVE_STOP_RESTORED", timestamp=observed_at,
            order_id=getattr(stop_leg.matching_order, "order_id", None),
        )

        first_filled = self._durable_target_filled_quantity(
            state, "FIRST_TARGET",
        )
        configured_target = int(
            (
                Decimal(quantity)
                * self.config.trade_management.first_target_exit_percent
            ).to_integral_value(rounding=ROUND_FLOOR)
        )
        state.first_quantity = max(state.first_quantity, configured_target)
        durable_first_complete = (
            state.first_quantity > 0
            and first_filled >= state.first_quantity
        )
        if state.first_taken and not durable_first_complete:
            self._record_management_event(
                state,
                "RECOVERED_FIRST_TARGET_STATE_CORRECTED",
                timestamp=observed_at,
                target_stage="FIRST_TARGET",
            )
        state.first_taken = durable_first_complete

        target_filled = first_filled
        target_allocation = state.first_quantity
        if not state.first_taken:
            target_role = "FIRST_TARGET"
        else:
            second_filled = self._durable_target_filled_quantity(
                state, "SECOND_TARGET",
            )
            durable_second_complete = (
                state.second_quantity > 0
                and second_filled >= state.second_quantity
            )
            state.second_taken = durable_second_complete
            if state.second_quantity > 0 and not state.second_taken:
                target_role = "SECOND_TARGET"
                target_filled = second_filled
                target_allocation = state.second_quantity

        if target_role is not None:
            desired_target_quantity = min(
                max(0, target_allocation - target_filled), quantity,
            )
            if desired_target_quantity <= 0:
                return incomplete("TARGET_QUANTITY_INVALID")
            try:
                desired_target_price = state.signal.target_levels[
                    0 if target_role == "FIRST_TARGET" else 1
                ]
            except (AttributeError, IndexError, TypeError):
                desired_target_price = None
            if desired_target_price is None or desired_target_price <= 0:
                return incomplete("TARGET_PRICE_UNAVAILABLE")
            if self._has_target_lifecycle_mismatch(state, target_role):
                return incomplete("TARGET_LIFECYCLE_MISMATCH")

            prior_target = self._durable_exit_leg(
                state,
                target_role,
                desired_quantity=desired_target_quantity,
                desired_price=desired_target_price,
            )
            target_result: object | None = None
            if not prior_target.is_exact:
                target_result = self._submit_exit(
                    state,
                    desired_target_price,
                    desired_target_quantity,
                    target_role,
                    target_stage_only=target_role == "FIRST_TARGET",
                )
            durable_target = self._durable_exit_leg(
                state,
                target_role,
                desired_quantity=desired_target_quantity,
                desired_price=desired_target_price,
            )
            if not durable_target.is_exact:
                if self._has_target_lifecycle_mismatch(state, target_role):
                    return incomplete("TARGET_LIFECYCLE_MISMATCH")
                # Recovery must not preserve an intention-only target as a
                # working lifecycle. A later pass remains free to retry it.
                if state.exit_reason == target_role:
                    state.exit_reason = None
                    state.exit_price = None
                if state.active_exit_role == target_role:
                    state.active_exit_role = None
                    state.active_exit_order_id = None
                submitted = (
                    target_result.state in {
                        PaperExitSubmissionState.SUBMITTED,
                        PaperExitSubmissionState.WORKING,
                    }
                    if isinstance(target_result, PaperExitSubmissionDecision)
                    else bool(target_result)
                )
                failure_reason = (
                    target_result.failure_reason.value
                    if isinstance(target_result, PaperExitSubmissionDecision)
                    and target_result.failure_reason is not None
                    else None
                )
                submission_state = (
                    target_result.state.value
                    if isinstance(target_result, PaperExitSubmissionDecision)
                    else None
                )
                return incomplete(
                    "TARGET_NOT_DURABLE"
                    if submitted else "TARGET_SUBMISSION_FAILED",
                    target_submission_failure_reason=failure_reason,
                    target_submission_state=submission_state,
                    target_submission_role=target_role,
                )

            # Target creation can atomically replace its correlated stop.
            # Re-read both legs and only repair the stop when the durable
            # bracket does not already contain valid protection.
            stop_leg = self._durable_exit_leg(
                state, "STOP", desired_quantity=quantity,
                desired_price=state.stop,
            )
            if not stop_leg.is_exact:
                self._submit_exit(state, state.stop, quantity, "STOP")
                stop_leg = self._durable_exit_leg(
                    state, "STOP", desired_quantity=quantity,
                    desired_price=state.stop,
                )
                durable_target = self._durable_exit_leg(
                    state,
                    target_role,
                    desired_quantity=desired_target_quantity,
                    desired_price=desired_target_price,
                )
            if not stop_leg.is_exact:
                state.protection_reconciled = False
                return incomplete("STOP_NOT_DURABLE")
            if not durable_target.is_exact:
                return incomplete("TARGET_NOT_DURABLE")

            state.active_exit_role = target_role
            state.active_exit_order_id = getattr(
                durable_target.matching_order, "order_id", None,
            )
            state.exit_reason = target_role
            state.exit_price = desired_target_price
            if prior_target.working_quantity == 0:
                self._record_management_event(
                    state,
                    "PROFIT_TARGET_RESTORED",
                    timestamp=observed_at,
                    target_stage=target_role,
                    desired_target_qty=desired_target_quantity,
                    desired_target_price=desired_target_price,
                    target_price=desired_target_price,
                    order_id=state.active_exit_order_id,
                )
                self._record_management_event(
                    state, "RECOVERED_PROFIT_TARGET_STAGED",
                    timestamp=observed_at,
                    target_stage=target_role,
                    desired_target_qty=desired_target_quantity,
                    desired_target_price=desired_target_price,
                    target_price=desired_target_price,
                    order_id=state.active_exit_order_id,
                )
            self._record_management_event(
                state,
                "PROFIT_TARGET_RECONCILED",
                timestamp=observed_at,
                target_stage=target_role,
                desired_target_qty=desired_target_quantity,
                desired_target_price=desired_target_price,
                target_price=desired_target_price,
                order_id=state.active_exit_order_id,
            )
        else:
            # Runner state needs only its protective stop.  Any live target is
            # contradictory durable state and must not be reported complete.
            obsolete_target_qty = sum(
                self._durable_exit_leg(state, role).working_quantity
                for role in ("FIRST_TARGET", "SECOND_TARGET")
            )
            if obsolete_target_qty > 0:
                return incomplete("OBSOLETE_TARGET_WORKING")

        state.protection_reconciled = True
        self._record_management_event(
            state, "PROTECTIVE_STOP_RECONCILED", timestamp=observed_at,
            desired_target_qty=desired_target_quantity,
            desired_target_price=desired_target_price,
        )
        self._record_management_event(
            state, "BRACKET_RECONCILIATION_COMPLETE", timestamp=observed_at,
            desired_target_qty=desired_target_quantity,
            desired_target_price=desired_target_price,
        )
        if self.writer is not None:
            self.writer.submit(_management_context_record(
                symbol.strip().upper(), observed_at, state.signal, state,
                phase="MANAGING",
            ))
        return True

    def _paper_fill(self, state: _PaperState, timestamp, action: str, label: str,
                    price: Decimal, quantity: int) -> CaptureRecord:
        return CaptureRecord.create(
            CaptureRecordType.PAPER_FILL, state.signal.symbol, timestamp,
            {"action": action, "label": label, "fill_price": price,
             "filled_shares": quantity, "remaining_before": state.remaining,
             "lifecycle_id": lifecycle_identity(state.signal),
             "active_stop": state.stop, "source": "SIMULATED_PAPER",
             "live_execution_authorized": False,
             "authority": "ANALYTICAL_FORWARD_CAPTURE"},
            identity_parts=(action, label, str(state.last_bar_timestamp)),
        )

    def _start_counterfactual(self, candidate: MomentumCandidate) -> tuple[CaptureRecord, ...]:
        setup = candidate.setup
        assert setup is not None and setup.trigger is not None and setup.stop_price is not None
        if candidate.symbol in self._counterfactual:
            return ()
        state = _CounterState(candidate.symbol, candidate.timestamp, setup.trigger, setup.stop_price)
        self._counterfactual[candidate.symbol] = state
        return (CaptureRecord.create(
            CaptureRecordType.COUNTERFACTUAL, candidate.symbol, candidate.timestamp,
            {"action": "START", "setup": setup.setup_type.value,
             "momentum_score": candidate.score.total, "trigger": setup.trigger,
             "stop": setup.stop_price,
             "blocking_gates": _gate_diagnostics(candidate, self.config, account=None),
             "excluded_from_v1_performance": True},
            identity_parts=("START", setup.setup_type.value),
        ),)

    def _recover(self) -> None:
        all_attributed = tuple(
            records_with_configuration_fingerprint(self.store.records())
        )
        attributed = all_attributed
        if self.paper_campaign_id is not None:
            attributed = tuple(
                (record, fingerprint) for record, fingerprint in all_attributed
                if record.record_type not in {
                    CaptureRecordType.PAPER_FILL,
                    CaptureRecordType.MANAGEMENT_CONTEXT,
                }
                or record.payload.get("paper_campaign_id") == self.paper_campaign_id
            )
        records = tuple(
            record for record, fingerprint in attributed
            if self.configuration_fingerprint is not None
            and fingerprint == self.configuration_fingerprint
        )
        entry_lifecycles: dict[str, str] = {}
        entries_by_lifecycle: dict[str, list[CaptureRecord]] = {}
        contexts: dict[str, CaptureRecord] = {}
        fingerprints: dict[str, str | None] = {}
        lifecycle_evidence: dict[str, list[CaptureRecord]] = {}
        recovery_records: list[CaptureRecord] = []

        # Index immutable lifecycle identity without requiring the newest
        # signal schema. Legacy authoritative fills may lack strategy context
        # fields, but an explicit lifecycle remains safe evidence for finding
        # same-generation durable records.
        for record, fingerprint in all_attributed:
            fingerprints[record.record_id] = fingerprint
            payload = record.payload
            lifecycle = _persisted_lifecycle_id(record, payload)
            if lifecycle is not None:
                lifecycle_evidence.setdefault(lifecycle, []).append(record)
            if (
                record.record_type is CaptureRecordType.PAPER_FILL
                and payload.get("action") == "ENTRY"
            ):
                if lifecycle is None:
                    recovery_records.append(_recovery_data_quality_record(
                        record, None, "RECOVERY_LIFECYCLE_SKIPPED",
                        "LIFECYCLE_ID_UNRECOVERABLE",
                    ))
                    continue
                entry_lifecycles[record.record_id] = lifecycle
                entries_by_lifecycle.setdefault(lifecycle, []).append(record)
            elif record.record_type is CaptureRecordType.MANAGEMENT_CONTEXT:
                context_lifecycle = str(payload.get("lifecycle_id") or "").strip()
                if context_lifecycle:
                    contexts[context_lifecycle] = record

        recovery_payloads: dict[str, dict[str, object]] = {}
        recovery_sources: dict[str, str] = {}
        skipped_lifecycles: set[str] = set()
        for lifecycle, lifecycle_entries in entries_by_lifecycle.items():
            evidence = tuple(lifecycle_evidence.get(lifecycle, ()))
            context = contexts.get(lifecycle)
            for entry in lifecycle_entries:
                recovered_payload, source = _recover_entry_payload(
                    entry, evidence=evidence, context=context,
                )
                if recovered_payload is None:
                    skipped_lifecycles.add(lifecycle)
                    continue
                recovery_payloads[entry.record_id] = recovered_payload
                recovery_sources[entry.record_id] = source
            if lifecycle in skipped_lifecycles:
                recovery_records.append(_recovery_data_quality_record(
                    lifecycle_entries[-1], lifecycle,
                    "LEGACY_SESSION_UNRECOVERABLE",
                    "RECOVERY_LIFECYCLE_SKIPPED",
                ))
            elif any("session" not in entry.payload for entry in lifecycle_entries):
                representative = lifecycle_entries[-1]
                recovery_records.append(_recovery_data_quality_record(
                    representative, lifecycle, "LEGACY_SESSION_RECOVERED",
                    recovery_sources.get(representative.record_id, "SAME_LIFECYCLE"),
                ))
        # Fingerprint isolation remains the default.  A prior generation may
        # be resumed only when an immutable Warrior ENTRY fill and an active
        # management context prove the same lifecycle, stop, and target
        # model.  This is an explicit, auditable migration boundary; a bare
        # broker position can never create a Warrior state here.
        current_lifecycles = {
            entry_lifecycles[record.record_id]
            for record in records
            if record.record_type is CaptureRecordType.PAPER_FILL
            and record.payload.get("action") == "ENTRY"
            and record.record_id in recovery_payloads
        }
        current_symbols = {
            record.symbol
            for record in records
            if record.record_type is CaptureRecordType.PAPER_FILL
            and record.payload.get("action") == "ENTRY"
        }
        entries: dict[str, CaptureRecord] = {}
        for record, fingerprint in all_attributed:
            payload = record.payload
            if record.record_type is CaptureRecordType.PAPER_FILL and payload.get("action") == "ENTRY":
                lifecycle = entry_lifecycles.get(record.record_id)
                if lifecycle is not None and record.record_id in recovery_payloads:
                    entries[lifecycle] = record

        # A restart creates a new campaign, but an authoritative open position
        # can still belong to the most recent proven Warrior lifecycle from a
        # prior campaign. Recover at most one such lifecycle per symbol. Closed
        # history and bare broker positions never create management authority.
        recoverable_by_symbol: dict[
            str, tuple[CaptureRecord, CaptureRecord]
        ] = {}
        for lifecycle, context in contexts.items():
            if lifecycle in current_lifecycles:
                continue
            entry = entries.get(lifecycle)
            if entry is None or entry.symbol in current_symbols:
                continue
            context_fingerprint = fingerprints.get(context.record_id)
            if not self._compatible_recovery_context(
                entry, context, context_fingerprint,
                recovery_payloads.get(entry.record_id),
            ):
                continue
            symbol_quantity = (
                0 if self._paper_position_quantity_source is None else
                max(0, int(self._paper_position_quantity_source(entry.symbol)))
            )
            if symbol_quantity <= 0:
                continue
            previous = recoverable_by_symbol.get(entry.symbol)
            if previous is None or context.timestamp > previous[1].timestamp:
                recoverable_by_symbol[entry.symbol] = (entry, context)
        for entry, context in recoverable_by_symbol.values():
            records += (entry, context)
        for record in records:
            if record.record_type is not CaptureRecordType.MINUTE_BAR:
                continue
            try:
                self._seen_bars.add((
                    record.symbol,
                    datetime.fromisoformat(record.payload["bar_timestamp"]),
                ))
            except (KeyError, ValueError):
                continue
        for record in records:
            if record.record_type is not CaptureRecordType.STATE_TRANSITION:
                continue
            payload = record.payload
            try:
                self._last_transition[record.symbol] = ForwardTransition(payload["to"])
            except (KeyError, ValueError):
                continue
        for record in records:
            if record.record_type is not CaptureRecordType.COUNTERFACTUAL:
                continue
            payload = record.payload
            action = payload.get("action")
            if action == "START":
                self._counterfactual[record.symbol] = _CounterState(
                    record.symbol, record.timestamp, Decimal(payload["trigger"]),
                    Decimal(payload["stop"]),
                )
            elif action == "PATH" and record.symbol in self._counterfactual:
                state = self._counterfactual[record.symbol]
                state.bars_observed = int(payload["bars_observed"])
                state.last_bar_timestamp = datetime.fromisoformat(
                    payload["source_bar_timestamp"]
                )
            elif action == "END":
                self._counterfactual.pop(record.symbol, None)
        # Rebuild still-open paper states from immutable fills.
        for record in records:
            if record.record_type is not CaptureRecordType.PAPER_FILL:
                continue
            payload = record.payload
            if payload.get("action") == "ENTRY":
                recovered_payload = recovery_payloads.get(record.record_id)
                if recovered_payload is None:
                    continue
                try:
                    signal = _signal_from_entry(record, recovered_payload)
                    quantity = int(recovered_payload["filled_shares"])
                except (KeyError, TypeError, ValueError):
                    recovery_records.append(_recovery_data_quality_record(
                        record, entry_lifecycles.get(record.record_id),
                        "RECOVERY_LIFECYCLE_SKIPPED",
                        "ENTRY_DESERIALIZATION_INVALID",
                    ))
                    continue
                first = int((Decimal(quantity) * self.config.trade_management.first_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
                second = int((Decimal(quantity) * self.config.trade_management.second_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
                self._paper[record.symbol] = _PaperState(
                    signal, Decimal(recovered_payload["fill_price"]), quantity, quantity,
                    Decimal(recovered_payload["structural_stop"]), first, second, quantity,
                )
                if self.strategy_ownership is not None:
                    self.strategy_ownership.acquire(
                        record.symbol, StrategyOwner.WARRIOR_MOMENTUM,
                        lifecycle_identity(signal),
                    )
                self._paper[record.symbol].risk_budget = Decimal(
                    recovered_payload.get(
                        "risk_dollars", signal.risk_per_share * quantity,
                    )
                )
            elif record.symbol in self._paper:
                state = self._paper[record.symbol]
                quantity = int(payload["filled_shares"])
                price = Decimal(payload["fill_price"])
                state.realized_pnl += (price - state.entry_price) * quantity
                state.remaining -= quantity
                label = payload.get("label")
                state.first_taken |= label == "FIRST_TARGET"
                state.second_taken |= label == "SECOND_TARGET"
                if state.first_taken:
                    state.stop = max(state.stop, state.entry_price)
                if state.remaining <= 0:
                    self._paper.pop(record.symbol, None)
        for record in records:
            if record.record_type is not CaptureRecordType.MANAGEMENT_CONTEXT:
                continue
            payload = record.payload
            if payload.get("phase") == "CLOSED":
                authoritative = (
                    0 if self._paper_position_quantity_source is None
                    else max(0, int(self._paper_position_quantity_source(record.symbol)))
                )
                if authoritative <= 0:
                    self._paper.pop(record.symbol, None)
                else:
                    state = self._paper.get(record.symbol)
                    if state is not None:
                        state.authoritative_position_seen = True
                        state.remaining = authoritative
                        self._last_transition[record.symbol] = ForwardTransition.PAPER_EXIT
                continue
            state = self._paper.get(record.symbol)
            if state is None:
                continue
            try:
                state.stop = Decimal(payload["stop"])
                state.prior_low = None if payload.get("prior_low") is None else Decimal(payload["prior_low"])
                state.minimum_low = None if payload.get("minimum_low") is None else Decimal(payload["minimum_low"])
                state.maximum_high = None if payload.get("maximum_high") is None else Decimal(payload["maximum_high"])
                state.peak_price = None if payload.get("peak_price") is None else Decimal(payload["peak_price"])
                state.peak_r = None if payload.get("peak_r") is None else Decimal(payload["peak_r"])
                state.current_r = None if payload.get("current_r") is None else Decimal(payload["current_r"])
                state.giveback_r = None if payload.get("giveback_r") is None else Decimal(payload["giveback_r"])
                state.giveback_fraction_of_peak = None if payload.get("giveback_fraction_of_peak") is None else Decimal(payload["giveback_fraction_of_peak"])
                state.profit_defense_armed = bool(payload.get("profit_defense_armed", False))
                state.profit_defense_stop_tightened = bool(payload.get("profit_defense_stop_tightened", False))
                state.profit_defense_runner_exit = bool(payload.get("profit_defense_runner_exit", False))
                state.profit_defense_last_action = payload.get("profit_defense_last_action")
                state.initial_stop = (
                    state.signal.stop_price
                    if payload.get("initial_stop") is None
                    else Decimal(payload["initial_stop"])
                )
                state.peak_executable_bid = (
                    None if payload.get("peak_executable_bid") is None
                    else Decimal(payload["peak_executable_bid"])
                )
                state.peak_executable_pnl = Decimal(
                    payload.get("peak_executable_pnl", "0")
                )
                state.peak_executable_r = (
                    None if payload.get("peak_executable_r") is None
                    else Decimal(payload["peak_executable_r"])
                )
                state.realized_from_partials = Decimal(
                    payload.get("realized_from_partials", "0")
                )
                state.current_secured_profit = Decimal(
                    payload.get("current_secured_profit", "0")
                )
                state.peak_to_current_giveback = Decimal(
                    payload.get("peak_to_current_giveback", "0")
                )
                state.profit_harvest_stage = int(
                    payload.get("profit_harvest_stage", 0)
                )
                state.pending_profit_harvest_role = payload.get(
                    "pending_profit_harvest_role"
                )
                add_on = payload.get("add_on")
                if isinstance(add_on, dict):
                    add_on_signal = replace(
                        state.signal,
                        timestamp=datetime.fromisoformat(str(add_on["timestamp"])),
                        entry_trigger=Decimal(add_on["entry_price"]),
                        reference_price=Decimal(add_on["entry_price"]),
                        stop_price=Decimal(add_on["stop"]),
                        risk_per_share=Decimal(add_on["risk_per_share"]),
                    )
                    state.add_on = _AddOnLeg(
                        str(add_on["add_on_id"]), str(add_on["parent_lifecycle_id"]),
                        add_on_signal, int(add_on["requested_quantity"]),
                        int(add_on.get("filled_quantity", 0)),
                        int(add_on.get("remaining", 0)), bool(add_on.get("active", True)),
                        None if add_on.get("peak_price") is None else Decimal(add_on["peak_price"]),
                        None if add_on.get("peak_r") is None else Decimal(add_on["peak_r"]),
                        None if add_on.get("current_r") is None else Decimal(add_on["current_r"]),
                        Decimal(add_on["stop"]), Decimal(add_on.get("risk_consumed", "0")),
                    )
                    state.add_on_used = True
                state.first_taken = bool(payload.get("first_taken", False))
                state.second_taken = bool(payload.get("second_taken", False))
                state.remaining = int(payload.get("remaining", state.remaining))
                state.managed_quantity = int(payload.get(
                    "managed_quantity", state.managed_quantity or state.remaining
                ))
                state.authoritative_position_seen = bool(
                    payload.get("authoritative_position_seen", False)
                )
                if self._paper_position_quantity_source is not None:
                    authoritative = max(
                        0, int(self._paper_position_quantity_source(record.symbol))
                    )
                    if authoritative > 0:
                        state.authoritative_position_seen = True
                        state.remaining = authoritative
                state.exit_reason = payload.get("exit_reason")
                state.exit_price = (
                    None if payload.get("exit_price") is None
                    else Decimal(payload["exit_price"])
                )
                state.active_exit_role = payload.get("active_exit_role")
                state.active_exit_order_id = payload.get("active_exit_order_id")
                state.protective_stop_activated_at = (
                    None if payload.get("protective_stop_activated_at") is None
                    else datetime.fromisoformat(payload["protective_stop_activated_at"])
                )
                self._last_transition.setdefault(
                    record.symbol, ForwardTransition.PAPER_ENTRY,
                )
            except (KeyError, TypeError, ValueError):
                self._paper.pop(record.symbol, None)
        if recovery_records:
            self._submit_records(tuple(recovery_records))

    def _compatible_recovery_context(
        self, entry: CaptureRecord, context: CaptureRecord,
        context_fingerprint: str | None,
        recovered_entry_payload: dict[str, object] | None = None,
    ) -> bool:
        """Permit only structurally proven same-lifecycle migration."""
        if context_fingerprint is None or entry.symbol != context.symbol:
            return False
        entry_payload = recovered_entry_payload or entry.payload
        context_payload = context.payload
        entry_lifecycle = _persisted_lifecycle_id(entry, entry_payload)
        if entry_lifecycle is None:
            return False
        if (
            context_payload.get("environment") != "PAPER"
            or context_payload.get("strategy") != "WARRIOR_MOMENTUM_V1"
            or context_payload.get("phase") not in {"MANAGING", "EXIT_WORKING"}
            or context_payload.get("lifecycle_id") != entry_lifecycle
            or context_payload.get("structural_stop") != entry_payload.get("structural_stop")
            or context_payload.get("planned_entry") != entry_payload.get("fill_price")
        ):
            return False
        try:
            trigger = Decimal(entry_payload["entry_trigger"])
            risk = Decimal(entry_payload["risk_per_share"])
            targets = tuple(Decimal(item) for item in entry_payload["targets"])
            expected = (trigger + risk, trigger + risk * 2, trigger + risk * 3)
        except (KeyError, TypeError, ValueError):
            return False
        return targets == expected and context_payload.get("stop") is not None


def _bar_record(bar: MinuteBar, observed_at: datetime) -> CaptureRecord:
    return CaptureRecord.create(
        CaptureRecordType.MINUTE_BAR, bar.symbol, observed_at,
        {"bar_timestamp": bar.timestamp, "interval": "1m", "completed": True,
         "completion_timestamp": bar.timestamp + timedelta(minutes=1),
         "observation_timestamp": observed_at,
         "open": bar.open, "high": bar.high, "low": bar.low,
         "close": bar.close, "volume": bar.volume},
        identity_parts=(bar.timestamp.isoformat(),),
    )


def _execution_gate_record(
    signal: MomentumEntrySignal,
    decision: PaperEntryAuthorizationDecision,
    symbol_authorization: PaperSymbolAuthorization | None = None,
) -> CaptureRecord:
    """Mirror the authoritative PAPER boundary without influencing it."""

    return CaptureRecord.create(
        CaptureRecordType.EXECUTION_GATE_DECISION,
        signal.symbol,
        signal.timestamp,
        {
            "authority": "OBSERVATION_ONLY",
            "strategy": "WARRIOR_MOMENTUM_V1",
            "lifecycle": decision.lifecycle_id,
            "setup": signal.setup_type.value,
            "technical_state": CandidateStatus.ENTRY_READY.value,
            "entry_trigger": signal.entry_trigger,
            "structural_stop": signal.stop_price,
            "reference_price": signal.reference_price,
            "symbol_authorization_mode": (
                None if symbol_authorization is None
                else symbol_authorization.mode.value
            ),
            "symbol_authorization_source": (
                None if symbol_authorization is None
                else symbol_authorization.source.value
            ),
            "result": decision.result.value,
            "final_reason": decision.reason.value,
            "gates": tuple({
                "gate": item.gate,
                "passed": item.passed,
                "observed": item.observed,
                "required": item.required,
            } for item in decision.gates),
            "order_constructed": decision.order_constructed,
            "submission_attempted": decision.submission_attempted,
            "placement_decision": decision.placement_decision,
        },
        identity_parts=(decision.lifecycle_id, decision.result.value),
    )


def _prebridge_execution_gate_record(
    candidate: MomentumCandidate,
    signal: MomentumEntrySignal,
    value: PointInTimeObservation,
    account: PaperAccountContext | None,
    *,
    config: WarriorMomentumConfig,
    stale_after: Decimal,
    execution_permitted: bool,
    existing_execution_reason: PaperEntryAuthorizationReason | None = None,
) -> CaptureRecord:
    """Explain a refusal/deferment before the PAPER bridge was invoked."""

    symbol_authorization = (
        None if account is None
        else _paper_symbol_authorization(signal, account)
    )
    gates = tuple(
        PaperEntryGateDecision(
            str(item["gate"]), bool(item["passed"]),
            str(item["observed"]), str(item["limit"]),
        )
        for item in (
            *_gate_diagnostics(candidate, config, account),
            *(() if account is None else _account_gate_diagnostics(
                signal, account, symbol_authorization,
            )),
        )
    )
    stale = (
        value.quote_freshness_seconds is None
        or value.last_price_freshness_seconds is None
        or value.quote_freshness_seconds > stale_after
        or value.last_price_freshness_seconds > stale_after
        or ReasonCode.STALE_MARKET_DATA in candidate.reason_codes
    )
    if existing_execution_reason is not None:
        result = PaperEntryAuthorizationResult.REFUSED
        reason = existing_execution_reason
        gates = (*gates, PaperEntryGateDecision(
            "existing_execution_owner", False,
            existing_execution_reason.value, "CLEAR",
        ))
    elif stale:
        result = PaperEntryAuthorizationResult.DEFERRED
        reason = PaperEntryAuthorizationReason.EXECUTION_DATA_UNAVAILABLE
    elif not execution_permitted:
        result = PaperEntryAuthorizationResult.REFUSED
        reason = PaperEntryAuthorizationReason.EXECUTION_NOT_PERMITTED
    elif not value.halt_state_known:
        result = PaperEntryAuthorizationResult.DEFERRED
        reason = PaperEntryAuthorizationReason.HALT_UNKNOWN
    elif account is None:
        result = PaperEntryAuthorizationResult.DEFERRED
        reason = PaperEntryAuthorizationReason.ACCOUNT_NOT_READY
    elif not symbol_authorization.authorized:
        result = PaperEntryAuthorizationResult.REFUSED
        reason = PaperEntryAuthorizationReason.SYMBOL_NOT_ALLOWED
    elif account.broker_restriction:
        result = PaperEntryAuthorizationResult.REFUSED
        reason = PaperEntryAuthorizationReason.BROKER_RESTRICTED
    elif not account.risk_engine_approved:
        result = PaperEntryAuthorizationResult.REFUSED
        reason = PaperEntryAuthorizationReason.RISK_REJECTED
    elif account.buying_power < signal.reference_price:
        result = PaperEntryAuthorizationResult.REFUSED
        reason = PaperEntryAuthorizationReason.BUYING_POWER_INSUFFICIENT
    elif (
        account.exposure_limit is not None
        and account.existing_exposure >= account.exposure_limit
    ):
        result = PaperEntryAuthorizationResult.REFUSED
        reason = PaperEntryAuthorizationReason.EXPOSURE_LIMIT
    else:
        # Every observable pre-bridge gate passed, but no authoritative bridge
        # decision was returned on this observation.  Never manufacture a risk
        # rejection to fill that evidence gap.
        result = PaperEntryAuthorizationResult.DEFERRED
        reason = PaperEntryAuthorizationReason.AUTHORIZATION_OUTCOME_UNAVAILABLE
        gates = (*gates, PaperEntryGateDecision(
            "execution_boundary_observed", False, "UNAVAILABLE", "AVAILABLE",
        ))
    decision = PaperEntryAuthorizationDecision(
        result, reason, signal.symbol, lifecycle_identity(signal), gates,
    )
    return _execution_gate_record(signal, decision, symbol_authorization)


def _management_context_record(
    symbol: str,
    timestamp: datetime,
    signal: MomentumEntrySignal,
    state: _PaperState,
    *,
    phase: str = "MANAGING",
) -> CaptureRecord:
    """Persist only strategy-management context, never execution authority."""
    return CaptureRecord.create(
        CaptureRecordType.MANAGEMENT_CONTEXT, symbol, timestamp,
        {
            "environment": "PAPER",
            "strategy": "WARRIOR_MOMENTUM_V1",
            "lifecycle_id": lifecycle_identity(signal),
            # Immutable signal provenance makes future schema evolution
            # recoverable without consulting wall-clock time or another
            # generation.
            "session": signal.session,
            "momentum_score": signal.momentum_score,
            "catalyst_state": signal.catalyst_state.value,
            "relative_volume": signal.relative_volume,
            "float_shares": signal.float_shares,
            "spread_percent": signal.spread_percent,
            "setup": signal.setup_type.value,
            "entry_timestamp": signal.timestamp,
            "planned_entry": state.entry_price,
            "structural_stop": signal.stop_price,
            "initial_stop": state.initial_stop,
            "stop": state.stop,
            "prior_low": state.prior_low,
            "minimum_low": state.minimum_low,
            "maximum_high": state.maximum_high,
            "peak_price": state.peak_price,
            "peak_r": state.peak_r,
            "current_r": state.current_r,
            "giveback_r": state.giveback_r,
            "giveback_fraction_of_peak": state.giveback_fraction_of_peak,
            "profit_defense_armed": state.profit_defense_armed,
            "profit_defense_stop_tightened": state.profit_defense_stop_tightened,
            "profit_defense_runner_exit": state.profit_defense_runner_exit,
            "profit_defense_last_action": state.profit_defense_last_action,
            "peak_executable_bid": state.peak_executable_bid,
            "peak_executable_pnl": state.peak_executable_pnl,
            "peak_executable_r": state.peak_executable_r,
            "realized_from_partials": state.realized_from_partials,
            "current_secured_profit": state.current_secured_profit,
            "peak_to_current_giveback": state.peak_to_current_giveback,
            "profit_harvest_stage": state.profit_harvest_stage,
            "pending_profit_harvest_role": state.pending_profit_harvest_role,
            "adaptive_exit": (
                None if state.adaptive_exit_assessment is None else {
                    "tighten_giveback_r": state.adaptive_exit_assessment.tighten_giveback_r,
                    "runner_exit_giveback_r": state.adaptive_exit_assessment.runner_exit_giveback_r,
                    "pressure_score": state.adaptive_exit_assessment.pressure_score,
                    "volatility_r": state.adaptive_exit_assessment.volatility_r,
                    "reasons": state.adaptive_exit_assessment.reasons,
                }
            ),
            "add_on": None if state.add_on is None else {
                "add_on_id": state.add_on.add_on_id,
                "parent_lifecycle_id": state.add_on.parent_lifecycle_id,
                "timestamp": state.add_on.signal.timestamp,
                "entry_price": state.add_on.signal.entry_trigger,
                "stop": state.add_on.stop,
                "risk_per_share": state.add_on.signal.risk_per_share,
                "requested_quantity": state.add_on.requested_quantity,
                "filled_quantity": state.add_on.filled_quantity,
                "remaining": state.add_on.remaining,
                "active": state.add_on.active,
                "peak_price": state.add_on.peak_price,
                "peak_r": state.add_on.peak_r,
                "current_r": state.add_on.current_r,
                "risk_consumed": state.add_on.risk_consumed,
            },
            "add_on_used": state.add_on_used,
            "first_taken": state.first_taken,
            "second_taken": state.second_taken,
            "managed_quantity": state.managed_quantity,
            "remaining": state.remaining,
            "authoritative_position_seen": state.authoritative_position_seen,
            "exit_reason": state.exit_reason,
            "exit_price": state.exit_price,
            "active_exit_role": state.active_exit_role,
            "active_exit_order_id": state.active_exit_order_id,
            "protective_stop_activated_at": state.protective_stop_activated_at,
            "phase": phase,
        },
        identity_parts=(lifecycle_identity(signal), phase, timestamp.isoformat()),
    )


def _discovery_record(value: PointInTimeObservation, candidate: MomentumCandidate) -> CaptureRecord:
    observation = value.observation
    spread = candidate.spread_percent
    return CaptureRecord.create(
        CaptureRecordType.DISCOVERY, candidate.symbol, candidate.timestamp,
        {"policy_version": candidate.policy_version,
         "discovery_status": "PASSED" if candidate.discovery_qualified else "BLOCKED",
         "session": candidate.session, "last_price": candidate.price,
         "percentage_change": candidate.percentage_change,
         "bid": observation.bid, "ask": observation.ask, "spread_percent": spread,
         "volume": candidate.volume, "relative_volume": candidate.relative_volume,
         "average_volume": observation.average_30_day_volume,
         "dollar_volume": candidate.dollar_volume, "float_value": candidate.float_shares,
         "float_provenance": value.float_provenance.value,
         "catalyst_state": candidate.catalyst_status.value,
         "catalyst_type": candidate.catalyst_type.value,
         "catalyst_timestamp": value.catalyst_event_timestamp,
         "catalyst_date": value.catalyst_event_date,
         "catalyst_source": value.catalyst_source,
         "tradable": candidate.tradable, "halted": candidate.halted,
         "halt_state_known": value.halt_state_known,
         "momentum_score": candidate.score.total,
         "stocks_in_play": tuple(item.value for item in candidate.stocks_in_play)},
    )


def _bind_decision_generation(
    candidate: MomentumCandidate,
    value: PointInTimeObservation,
) -> MomentumCandidate:
    """Bind one decision to one immutable observation/quote generation."""
    scanner_timestamp = value.observation.timestamp
    decision_timestamp = value.evaluation_timestamp or scanner_timestamp
    quote_timestamp = value.quote_observed_at or value.observation.quote_timestamp
    setup = candidate.setup
    episode = None if setup is None else (
        setup.structural_episode_id or setup.taxonomy_execution_identity
    )
    identity = "|".join((
        candidate.symbol,
        scanner_timestamp.isoformat(),
        decision_timestamp.isoformat(),
        "" if quote_timestamp is None else quote_timestamp.isoformat(),
        "" if episode is None else str(episode),
    ))
    return replace(
        candidate,
        decision_generation_id=sha256(identity.encode("utf-8")).hexdigest()[:24],
        scanner_observation_timestamp=scanner_timestamp,
        warrior_observation_timestamp=decision_timestamp,
        decision_timestamp=decision_timestamp,
        decision_quote_timestamp=quote_timestamp,
    )


def _opportunity_authorization_record(
    assessment: WarriorOpportunityAssessment, *, authorized: bool,
    reason: str, risk_result: str, sizing_result: str,
) -> CaptureRecord:
    """Durable sanitized generation-bound authorization provenance."""
    return CaptureRecord.create(
        CaptureRecordType.STATE_TRANSITION,
        assessment.symbol,
        assessment.decision_timestamp,
        {
            "from": WarriorOpportunityState.EXECUTABLE.value,
            "to": WarriorOpportunityState.AUTHORIZATION_EVALUATED.value,
            "strategy": assessment.strategy,
            "setup": assessment.setup_family,
            "generation_id": assessment.generation_id,
            "lifecycle_id": assessment.lifecycle_id,
            "trigger": assessment.structural_trigger,
            "structural_stop": assessment.structural_stop,
            "execution_ask": assessment.executable_entry,
            "execution_bid": assessment.executable_bid,
            "provider_last_timestamp": assessment.provider_last_timestamp,
            "provider_bid_timestamp": assessment.provider_bid_timestamp,
            "provider_ask_timestamp": assessment.provider_ask_timestamp,
            "decision_timestamp": assessment.decision_timestamp,
            "execution_quote_timestamp": assessment.execution_quote_timestamp,
            "spread_percent": assessment.spread_percent,
            "opportunity_result": assessment.adaptive_result.value,
            "authorization_result": "AUTHORIZED" if authorized else "REJECTED",
            "authorization_reason": _bounded_reason(reason),
            "risk_result": _bounded_reason(risk_result),
            "sizing_result": _bounded_reason(sizing_result),
        },
        identity_parts=(
            assessment.generation_id, "AUTHORIZATION_EVALUATED",
            "AUTHORIZED" if authorized else "REJECTED",
        ),
    )


def _bounded_reason(value: str) -> str:
    normalized = str(value or "UNSPECIFIED").strip().upper()
    if (
        0 < len(normalized) <= 64
        and all(character.isalnum() or character == "_" for character in normalized)
    ):
        return normalized
    return "OTHER"


def _decision_record(value, candidate, completed, features) -> CaptureRecord:
    from .setup_diagnostics import production_setup_diagnostics

    observation = value.observation
    setup = candidate.setup
    payload = {
        "policy_version": candidate.policy_version,
        "discovery_status": "PASSED" if candidate.discovery_qualified else "BLOCKED",
        "observation_status": "ELIGIBLE" if candidate.observation_eligible else "REMOVED",
        "observation_blockers": tuple(code.value for code in candidate.observation_blockers),
        "entry_status": "READY" if candidate.status is CandidateStatus.ENTRY_READY else "BLOCKED",
        "decision_id": candidate.decision_generation_id,
        "decision_timestamp": candidate.decision_timestamp or candidate.timestamp,
        "scanner_observation_timestamp": candidate.scanner_observation_timestamp,
        "warrior_observation_timestamp": candidate.warrior_observation_timestamp,
        "decision_quote_timestamp": candidate.decision_quote_timestamp,
        "evaluation_timestamp": value.evaluation_timestamp,
        "last_price_timestamp": value.last_price_observed_at,
        "quote_timestamp": value.quote_observed_at,
        "last_price_age_seconds": value.last_price_freshness_seconds,
        "quote_age_seconds": value.quote_freshness_seconds,
        "observation": {
            "price": observation.price, "previous_close": observation.previous_close,
            "current_volume": observation.current_volume,
            "average_30_day_volume": observation.average_30_day_volume,
            "float_shares": observation.float_shares, "bid": observation.bid,
            "ask": observation.ask, "catalyst": observation.catalyst.value,
            "catalyst_status": observation.catalyst_status.value,
            "tradable": observation.tradable, "halted": observation.halted,
            "asset_class": observation.asset_class.value,
        },
        "session": value.session,
        "bar_timestamps": tuple(bar.timestamp for bar in completed),
        "features": None if features is None else {
            "vwap": features.vwap, "session_high": features.session_high,
            "session_low": features.session_low, "rolling_high": features.rolling_high,
            "rolling_low": features.rolling_low,
            "rolling_change_percent": features.rolling_change_percent,
            "rolling_volume": features.rolling_volume,
            "volume_acceleration": features.volume_acceleration,
            "distance_from_vwap_percent": features.distance_from_vwap_percent,
            "distance_from_hod_percent": features.distance_from_hod_percent,
            "pullback_depth_percent": features.pullback_depth_percent,
            "consolidation_duration": features.consolidation_duration,
            "breakout_level": features.breakout_level,
            "breakout_volume_ratio": features.breakout_volume_ratio,
        },
        "score": candidate.score.total,
        "score_components": candidate.score.components,
        "stocks_in_play": tuple(item.value for item in candidate.stocks_in_play),
        "status": candidate.status.value,
        "setup": None if setup is None else {
            "type": setup.setup_type.value, "state": setup.state.value,
            "score": setup.score, "trigger": setup.trigger,
            "stop_price": setup.stop_price,
            "stop_model": None if setup.stop_model is None else setup.stop_model.value,
            "resistance": setup.resistance,
        },
        "canonical_setup_evidence": None if candidate.setup_evidence is None else {
            "symbol": candidate.setup_evidence.symbol,
            "session": candidate.setup_evidence.session,
            "evaluation_timestamp": candidate.setup_evidence.evaluation_timestamp,
            "completed_bar_cutoff": candidate.setup_evidence.completed_bar_cutoff,
            "completed_bar_count": candidate.setup_evidence.completed_bar_count,
            "bar_timestamps": candidate.setup_evidence.bar_timestamps,
            "detector": candidate.setup_evidence.detector,
            "state": candidate.setup_evidence.state.value,
            "trigger": candidate.setup_evidence.trigger,
            "structural_stop": candidate.setup_evidence.structural_stop,
            "opportunity_id": candidate.setup_evidence.opportunity_id,
            "structural_invalidation": tuple(
                code.value for code in candidate.setup_evidence.structural_invalidation
            ),
        },
        "reason_codes": tuple(code.value for code in candidate.reason_codes),
        "setup_diagnostics": tuple(
            item.as_payload() for item in production_setup_diagnostics(completed)
        ),
    }
    return CaptureRecord.create(CaptureRecordType.DECISION, candidate.symbol,
                                candidate.timestamp, payload)


def _transition_record(candidate, transition, reasons, gates) -> CaptureRecord:
    return CaptureRecord.create(
        CaptureRecordType.STATE_TRANSITION, candidate.symbol, candidate.timestamp,
        {"policy_version": candidate.policy_version,
         "discovery_status": "PASSED" if candidate.discovery_qualified else "BLOCKED",
         "setup_status": "NO_SETUP" if candidate.setup is None else candidate.setup.state.value,
         "entry_status": "READY" if transition is ForwardTransition.ENTRY_READY else "BLOCKED",
         "to": transition.value, "reason_codes": reasons,
         "blocking_gates": tuple(gate for gate in gates if not gate["passed"])},
        identity_parts=(transition.value,),
    )


def _quality_record(value, completed, capture_config) -> CaptureRecord:
    observation = value.observation
    flags = {
        "missing_bid_ask": observation.bid is None or observation.ask is None,
        "stale_bid_ask": (
            value.quote_freshness_seconds is None
            or value.quote_freshness_seconds > capture_config.quote_stale_after_seconds
        ),
        "stale_last_price": (
            value.last_price_freshness_seconds is None
            or value.last_price_freshness_seconds
            > capture_config.quote_stale_after_seconds
        ),
        "missing_catalyst": observation.catalyst_status.value in {"UNKNOWN", "UNAVAILABLE"},
        "unknown_catalyst": observation.catalyst_status.value == "UNKNOWN",
        "unavailable_catalyst": observation.catalyst_status.value == "UNAVAILABLE",
        "missing_float": observation.float_shares is None,
        "proxy_float": value.float_provenance is FloatProvenance.MARKET_CAP_PRICE_PROXY,
        "missing_volume": not value.volume_known,
        "missing_historical_bars": not value.historical_bars_available or not completed,
        "halt_uncertainty": not value.halt_state_known,
    }
    return CaptureRecord.create(CaptureRecordType.DATA_QUALITY, observation.symbol,
                                observation.timestamp, flags)


def _gate_diagnostics(candidate, config, account):
    setup = candidate.setup
    risk = None
    if setup is not None and setup.trigger is not None and setup.stop_price is not None:
        risk = setup.trigger - setup.stop_price
    return (
        {"gate": "momentum_score", "passed": candidate.score.total >= config.entry.minimum_momentum_score,
         "observed": candidate.score.total, "limit": config.entry.minimum_momentum_score},
        {"gate": "setup", "passed": setup is not None and setup.state is SetupState.TRIGGERED,
         "observed": None if setup is None else setup.state.value, "limit": SetupState.TRIGGERED.value},
        {"gate": "execution_quality", "passed": candidate.execution_quality in {
             ExecutionQuality.EXCELLENT, ExecutionQuality.GOOD, ExecutionQuality.MARGINAL,
         }, "observed": candidate.execution_quality.value, "limit": "MARGINAL_OR_BETTER"},
        {"gate": "catalyst", "passed": not config.entry.require_catalyst_for_entry or candidate.catalyst_status.value == "TRUE",
         "observed": candidate.catalyst_status.value, "limit": "TRUE"},
        {"gate": "liquidity", "passed": execution_liquidity_ok(candidate, config),
         "observed": candidate.dollar_volume,
         "limit": (config.entry.minimum_dollar_volume
                   if not config.adaptive_context_enabled else "CURRENT_QUOTE_AND_SPREAD")},
        {"gate": "tradability", "passed": candidate.tradable, "observed": candidate.tradable, "limit": True},
        {"gate": "halt", "passed": not candidate.halted, "observed": candidate.halted, "limit": False},
        {"gate": "session", "passed": candidate.session in config.entry.allowed_sessions,
         "observed": candidate.session, "limit": tuple(sorted(config.entry.allowed_sessions))},
        {"gate": "risk_distance", "passed": risk is not None and ZERO < risk <= config.entry.maximum_risk_per_share,
         "observed": risk, "limit": config.entry.maximum_risk_per_share},
        {"gate": "market_data", "passed": ReasonCode.STALE_MARKET_DATA not in candidate.reason_codes,
         "observed": "STALE" if ReasonCode.STALE_MARKET_DATA in candidate.reason_codes else "LIVE",
         "limit": "LIVE"},
        {"gate": "paper_risk_context", "passed": account is not None and account.risk_engine_approved,
         "observed": None if account is None else account.risk_engine_approved, "limit": True},
    )


def _paper_symbol_authorization(
    signal: MomentumEntrySignal,
    account: PaperAccountContext | None,
) -> PaperSymbolAuthorization:
    """Authorize only the internally assessed Warrior PAPER signal boundary."""

    if account is None:
        return PaperSymbolAuthorization(
            False,
            PaperSymbolAuthorizationMode.STATIC_ALLOWLIST,
            PaperSymbolAuthorizationSource.NONE,
        )
    mode = account.symbol_authorization_mode
    if mode in {
        PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR,
        PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR_AND_QUICK_SCALPER,
    }:
        authorized = (
            signal.strategy_id == "WARRIOR_MOMENTUM_V1"
            and not signal.execution_authorized
        )
        return PaperSymbolAuthorization(
            authorized,
            mode,
            (
                PaperSymbolAuthorizationSource.DYNAMIC_WARRIOR_PAPER
                if authorized else PaperSymbolAuthorizationSource.NONE
            ),
        )
    authorized = signal.symbol in account.allowed_symbols
    return PaperSymbolAuthorization(
        authorized,
        mode,
        (
            PaperSymbolAuthorizationSource.STATIC_ALLOWLIST
            if authorized else PaperSymbolAuthorizationSource.NONE
        ),
    )


def _account_gate_diagnostics(
    signal, account,
    authorization: PaperSymbolAuthorization | None = None,
):
    authorization = authorization or _paper_symbol_authorization(signal, account)
    return (
        {"gate": "paper_symbol_authorization", "passed": authorization.authorized,
         "observed": authorization.source.value,
         "limit": authorization.mode.value},
        {"gate": "risk_engine", "passed": account.risk_engine_approved,
         "observed": account.risk_engine_approved, "limit": True},
        {"gate": "broker_restriction", "passed": not account.broker_restriction,
         "observed": account.broker_restriction, "limit": False},
        {"gate": "buying_power", "passed": account.buying_power >= signal.entry_trigger,
         "observed": account.buying_power, "limit": signal.entry_trigger},
        {"gate": "exposure", "passed": (
             account.exposure_limit is None
             or account.existing_exposure < account.exposure_limit
         ), "observed": account.existing_exposure, "limit": account.exposure_limit},
    )


_RECOVERY_SIGNAL_CONTEXT_FIELDS = (
    "session", "momentum_score", "catalyst_state", "relative_volume",
)


def _persisted_lifecycle_id(
    record: CaptureRecord, payload: dict[str, object],
) -> str | None:
    explicit = str(payload.get("lifecycle_id") or "").strip()
    if explicit:
        return explicit
    try:
        return lifecycle_identity(_signal_from_entry(record, payload))
    except (KeyError, TypeError, ValueError):
        return None


def _recover_entry_payload(
    entry: CaptureRecord, *, evidence: tuple[CaptureRecord, ...],
    context: CaptureRecord | None,
) -> tuple[dict[str, object] | None, str]:
    # Modern ENTRY wins. Missing values may come only from records explicitly
    # bound to this persisted lifecycle; current wall-clock time and unrelated
    # symbol decisions never participate.
    payload: dict[str, object] = dict(entry.payload)
    lifecycle = str(payload.get("lifecycle_id") or "").strip()
    if not lifecycle:
        try:
            _signal_from_entry(entry, payload)
        except (KeyError, TypeError, ValueError):
            return None, "LIFECYCLE_ID_UNRECOVERABLE"
        return payload, "MODERN_ENTRY"

    sibling_fills = tuple(
        record.payload for record in evidence
        if record.record_id != entry.record_id
        and record.record_type is CaptureRecordType.PAPER_FILL
        and record.payload.get("action") == "ENTRY"
        and str(record.payload.get("lifecycle_id") or "").strip() == lifecycle
    )
    context_payloads = (
        () if context is None
        or str(context.payload.get("lifecycle_id") or "").strip() != lifecycle
        else (context.payload,)
    )
    other_payloads = tuple(
        record.payload for record in evidence
        if record.record_type not in {
            CaptureRecordType.PAPER_FILL,
            CaptureRecordType.MANAGEMENT_CONTEXT,
        }
        and str(
            record.payload.get("lifecycle_id")
            or record.payload.get("generation_id")
            or ""
        ).strip() == lifecycle
    )
    source_name = "MODERN_ENTRY"
    for field in _RECOVERY_SIGNAL_CONTEXT_FIELDS:
        if payload.get(field) is not None:
            continue
        recovered = False
        for candidate_source, candidates in (
            ("SAME_LIFECYCLE_ENTRY", sibling_fills),
            ("SAME_LIFECYCLE_MANAGEMENT_CONTEXT", context_payloads),
            ("SAME_LIFECYCLE_PROVENANCE", other_payloads),
        ):
            values = [candidate.get(field) for candidate in candidates
                      if candidate.get(field) is not None]
            if not values:
                continue
            normalized = {str(value) for value in values}
            if len(normalized) != 1:
                return None, f"CONFLICTING_{field.upper()}"
            payload[field] = values[-1]
            if field == "session":
                source_name = candidate_source
            recovered = True
            break
        if not recovered:
            return None, f"{field.upper()}_UNRECOVERABLE"

    session = str(payload.get("session") or "").strip().upper()
    if session not in WARRIOR_ENTRY_ALLOWED_SESSIONS:
        return None, "SESSION_INVALID"
    payload["session"] = session
    try:
        _signal_from_entry(entry, payload)
    except (KeyError, TypeError, ValueError):
        return None, "ENTRY_DESERIALIZATION_INVALID"
    return payload, source_name


def _recovery_data_quality_record(
    entry: CaptureRecord, lifecycle: str | None, result: str, reason: str,
) -> CaptureRecord:
    return CaptureRecord.create(
        CaptureRecordType.DATA_QUALITY, entry.symbol, entry.timestamp,
        {
            "action": "RECOVERY",
            "strategy": "WARRIOR_MOMENTUM_V1",
            "lifecycle_id": lifecycle,
            "result": _bounded_reason(result),
            "reason": _bounded_reason(reason),
        },
        identity_parts=(
            "RECOVERY", str(lifecycle or entry.record_id),
            _bounded_reason(result),
        ),
    )


def _signal_from_entry(record, payload) -> MomentumEntrySignal:
    from app.momentum_scanner.models import CatalystStatus
    from .models import SetupType, StopModel
    return MomentumEntrySignal(
        "WARRIOR_MOMENTUM_V1", record.symbol, record.timestamp, payload["session"],
        Decimal(payload["momentum_score"]), SetupType(payload["setup"]),
        Decimal(payload["entry_trigger"]), Decimal(payload["fill_price"]),
        Decimal(payload["structural_stop"]), StopModel(payload["stop_model"]),
        Decimal(payload["risk_per_share"]), tuple(Decimal(item) for item in payload["targets"]),
        CatalystStatus(payload["catalyst_state"]), Decimal(payload["relative_volume"]),
        None if payload.get("float_shares") is None else Decimal(payload["float_shares"]),
        None if payload.get("spread_percent") is None else Decimal(payload["spread_percent"]),
        ZERO, ZERO, Decimal("0"), (), False,
        taxonomy_execution_identity=(
            None
            if not str(payload.get("lifecycle_id") or "").strip()
            else str(payload["lifecycle_id"]).strip()
        ),
    )


__all__ = ["WarriorForwardCaptureService"]
