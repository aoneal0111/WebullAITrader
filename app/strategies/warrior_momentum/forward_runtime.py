"""Point-in-time Warrior observation, paper lifecycle, and counterfactual sidecar."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from typing import Callable

from app.performance_diagnostics import performance_diagnostics
from app.configuration.models import PaperSymbolAuthorizationMode

from .configuration import WarriorMomentumConfig
from .features import build_features, completed_bars_as_of
from .forward_models import (
    CaptureRecord, CaptureRecordType, FloatProvenance,
    ForwardCaptureConfiguration, ForwardTransition, PaperAccountContext,
    PaperSymbolAuthorization, PaperSymbolAuthorizationSource,
    PointInTimeObservation,
    records_with_configuration_fingerprint,
)
from .autonomous_paper import lifecycle_identity
from .execution_quote import ExecutionQuoteSource
from .forward_queue import ForwardCaptureWriter
from .forward_store import ForwardCaptureStore
from .autonomous_paper import (
    PaperEntryAuthorizationDecision, PaperEntryAuthorizationReason,
    PaperEntryAuthorizationResult, PaperEntryGateDecision,
    PaperExitSubmissionDecision, PaperExitSubmissionState, lifecycle_identity, opportunity_identity,
    PaperEntryReplacementDecision, PaperEntryReplacementPolicy,
)
from .models import (
    CandidateStatus, MinuteBar, MomentumCandidate, MomentumEntrySignal,
    ReasonCode, SetupState,
)
from .risk import size_position
from .runtime import WarriorMomentumRuntime, entry_rejections
from .shadow_analysis import ShadowOpportunityAnalyzer
from .shadow_latched import (
    ShadowLatchedPlanResearch,
    ShadowLatchedTransition,
    ShadowMarketObservation,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def management_context_available(
    storage_path, symbol: str, lifecycle_id: str | None = None,
    configuration_fingerprint: str | None = None,
    *, allow_compatible_generation: bool = False,
) -> str | None:
    """Return the matching active PAPER lifecycle ID, when available.

    Matching is structural when a lifecycle is supplied; symbol is only the
    lookup partition and never the identity of an active trade.
    """
    try:
        store = ForwardCaptureStore(storage_path)
        records = store.records(symbol=symbol, record_type=CaptureRecordType.MANAGEMENT_CONTEXT)
        if configuration_fingerprint is not None:
            attributed = tuple(records_with_configuration_fingerprint(store.records()))
            records = tuple(
                record for record, fingerprint in attributed
                if record.symbol == symbol
                and record.record_type is CaptureRecordType.MANAGEMENT_CONTEXT
                and fingerprint == configuration_fingerprint
            )
            if not records and allow_compatible_generation:
                entries = {
                    str(item.payload.get("lifecycle_id") or lifecycle_identity(
                        _signal_from_entry(item, item.payload)
                    )): item
                    for item, _fingerprint in attributed
                    if item.symbol == symbol
                    and item.record_type is CaptureRecordType.PAPER_FILL
                    and item.payload.get("action") == "ENTRY"
                }
                compatible: list[CaptureRecord] = []
                for item, fingerprint in attributed:
                    payload = item.payload
                    entry = entries.get(str(payload.get("lifecycle_id")))
                    try:
                        trigger = Decimal(entry.payload["entry_trigger"])
                        risk = Decimal(entry.payload["risk_per_share"])
                        targets = tuple(Decimal(value) for value in entry.payload["targets"])
                    except (AttributeError, KeyError, TypeError, ValueError):
                        continue
                    if (
                        item.symbol == symbol
                        and item.record_type is CaptureRecordType.MANAGEMENT_CONTEXT
                        and fingerprint not in (None, configuration_fingerprint)
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
        for record in reversed(records):
            payload = record.payload
            candidate = payload.get("lifecycle_id")
            if lifecycle_id is not None and candidate != lifecycle_id:
                continue
            if payload.get("environment") == "PAPER" and bool(candidate):
                # The newest matching context owns recovery.  Never skip a
                # CLOSED record and revive an older MANAGING record.
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
    protective_stop_activated_at: datetime | None = None
    protection_reconciled: bool = False
    risk_budget: Decimal = ZERO
    add_on: _AddOnLeg | None = None
    add_on_used: bool = False


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
        execution_quote_source: ExecutionQuoteSource | None = None,
        execution_permitted: Callable[[], bool] | None = None,
        account_refresh_source: Callable[[], PaperAccountContext | None] | None = None,
        entry_value_observer: Callable[..., None] | None = None,
        configuration_fingerprint: str | None = None,
        paper_campaign_id: str | None = None,
        paper_add_on_submitter: Callable[..., object] | None = None,
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
        self._execution_quote_source = execution_quote_source
        self._execution_permitted = execution_permitted or (lambda: True)
        self._account_refresh_source = account_refresh_source
        self._entry_value_observer = entry_value_observer
        self.configuration_fingerprint = configuration_fingerprint
        self.paper_campaign_id = paper_campaign_id
        self._paper_add_on_submitter = paper_add_on_submitter
        self.runtime = WarriorMomentumRuntime(config)
        self._last_transition: dict[str, ForwardTransition] = {}
        self._seen_bars: set[tuple[str, datetime]] = set()
        self._paper: dict[str, _PaperState] = {}
        self._counterfactual: dict[str, _CounterState] = {}
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

    def observe(
        self, value: PointInTimeObservation,
        *, account: PaperAccountContext | None = None,
    ) -> tuple[MomentumCandidate, MomentumEntrySignal | None]:
        observation = value.observation
        symbol = observation.symbol.strip().upper()
        live_state = self._paper.get(symbol)
        if (
            live_state is not None
            and observation.price is not None
            and observation.timestamp >= live_state.signal.timestamp
        ):
            live_state.maximum_high = (
                observation.price if live_state.maximum_high is None
                else max(live_state.maximum_high, observation.price)
            )
            self._update_peak(live_state)
            if live_state.signal.risk_per_share > ZERO:
                live_state.current_r = (
                    observation.price - live_state.entry_price
                ) / live_state.signal.risk_per_share
                self._update_peak(live_state)
        completed = completed_bars_as_of(value.bars, observation.timestamp)
        for bar in completed:
            self.observe_market_bar(symbol, bar, observation.timestamp)
        candidate = self.runtime.discover(
            observation, completed, session=value.session,
        )
        assessed, signal = self.runtime.assess_entry(candidate)
        technical_signal = self.runtime.technical_entry_signal(candidate)
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
                value.delivery_age_seconds is not None
                and value.delivery_age_seconds
                > self.capture_config.quote_stale_after_seconds
            )
        )
        if processing_delayed:
            performance_diagnostics.increment("processing_delayed_events")
        if market_data_stale or processing_delayed:
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
                performance_diagnostics.mark_latency_trace_stage(
                    "execution_quote_requested", True
                )
                try:
                    refreshed = self._execution_quote_source(symbol)
                except Exception:
                    refreshed = None
                evaluated_at = (
                    (None if refreshed is None else refreshed.confirmed_at)
                    or value.evaluation_timestamp
                    or observation.timestamp
                )
                if refreshed is not None:
                    quote_age = Decimal(str((evaluated_at - refreshed.bid_timestamp).total_seconds()))
                    last_age = Decimal(str((evaluated_at - refreshed.last_timestamp).total_seconds()))
                    technical_minute = (
                        value.evaluation_timestamp or observation.timestamp
                    ).replace(second=0, microsecond=0)
                    if (
                        refreshed.symbol == symbol
                        and Decimal("0") <= quote_age <= self.capture_config.quote_stale_after_seconds
                        and Decimal("0") <= last_age <= self.capture_config.quote_stale_after_seconds
                        # Do not reuse setup geometry if a new minute could
                        # have completed while the bounded request was open.
                        and evaluated_at.replace(second=0, microsecond=0) == technical_minute
                    ):
                        refreshed_observation = replace(
                            observation, price=refreshed.last,
                            bid=refreshed.bid, ask=refreshed.ask,
                            quote_timestamp=refreshed.bid_timestamp,
                            last_price_timestamp=refreshed.last_timestamp,
                            quote_received_timestamp=refreshed.confirmed_at,
                            last_price_received_timestamp=refreshed.confirmed_at,
                            bid_size=None, ask_size=None,
                        )
                        refreshed_candidate = self.runtime.discover(
                            refreshed_observation, completed, session=value.session,
                        )
                        refreshed_assessed, refreshed_signal = self.runtime.assess_entry(
                            refreshed_candidate
                        )
                        if refreshed_signal is not None:
                            candidate = refreshed_candidate
                            assessed = refreshed_assessed
                            signal = refreshed_signal
                            observation = refreshed_observation
                            value = replace(
                                value, observation=refreshed_observation,
                                quote_observed_at=refreshed.bid_timestamp,
                                quote_freshness_seconds=quote_age,
                                last_price_observed_at=refreshed.last_timestamp,
                                last_price_freshness_seconds=last_age,
                                evaluation_timestamp=evaluated_at,
                                best_bid_size=None,
                                best_ask_size=None,
                                quote_provenance="SHARED_EXECUTION_QUOTE_CONFIRMATION",
                            )
                            if self._account_refresh_source is not None:
                                account = self._account_refresh_source()
                            if not self._execution_permitted():
                                signal = None
        if signal is not None:
            if self._account_refresh_source is not None:
                account = self._account_refresh_source()
            if not self._execution_permitted():
                assessed = replace(
                    assessed, status=CandidateStatus.INELIGIBLE_FOR_EXECUTION,
                    reason_codes=tuple(dict.fromkeys((
                        *assessed.reason_codes, ReasonCode.EXECUTION_NOT_ALLOWED,
                    ))),
                )
                signal = None
        if (
            signal is not None
            and account is not None
            and self._paper_entry_replacer is not None
            and self.config.adaptive_entry.enabled
            and signal.symbol in self._paper
        ):
            self._consider_adaptive_entry_replacement(
                value, assessed, signal, account,
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
            symbol_authorization = _paper_symbol_authorization(signal, account)
            if signal.symbol in self._paper:
                shadow_reasons.append(ReasonCode.EXECUTION_NOT_ALLOWED.value)
                records.append(_transition_record(
                    assessed, ForwardTransition.ENTRY_BLOCKED,
                    (ReasonCode.EXECUTION_NOT_ALLOWED.value,),
                    ({"gate": "existing_paper_position", "passed": False,
                      "observed": True, "limit": False},),
                ))
                signal = None
            elif not value.halt_state_known:
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
                shadow_reasons.append(ReasonCode.EXECUTION_NOT_ALLOWED.value)
                blocked = _transition_record(
                    assessed, ForwardTransition.ENTRY_BLOCKED,
                    (ReasonCode.EXECUTION_NOT_ALLOWED.value,),
                    _gate_diagnostics(assessed, self.config, account=None),
                )
                records.append(blocked)
                signal = None
            else:
                position = size_position(
                    signal, account_equity=account.equity,
                    buying_power=account.buying_power,
                    allowed_symbols=account.allowed_symbols,
                    existing_exposure=account.existing_exposure,
                    exposure_limit=account.exposure_limit,
                    risk_engine_approved=account.risk_engine_approved,
                    broker_restriction=account.broker_restriction,
                    config=self.config.risk,
                    symbol_authorized=symbol_authorization.authorized,
                )
                if position.approved:
                    entry_value_quantity = position.shares
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
                    if execution_record is not None:
                        records.append(execution_record)
                    # A configured execution bridge is authoritative for the
                    # entry boundary.  If it rejects the command, do not
                    # return an apparently executable signal to callers.
                    if self._paper_entry_submitter is not None and not entry_records:
                        shadow_reasons.append(ReasonCode.EXECUTION_NOT_ALLOWED.value)
                        signal = None
                else:
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
            try:
                execution_record = _prebridge_execution_gate_record(
                    assessed, technical_signal, value, account,
                    config=self.config,
                    stale_after=self.capture_config.quote_stale_after_seconds,
                    execution_permitted=self._execution_permitted(),
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
        self._submit_records(tuple(records))
        return assessed, signal

    def _consider_adaptive_entry_replacement(
        self,
        value: PointInTimeObservation,
        candidate: MomentumCandidate,
        signal: MomentumEntrySignal,
        account: PaperAccountContext,
    ) -> None:
        """Use one fresh observation to consider a bounded entry replacement."""
        state = self._paper.get(signal.symbol)
        ask = value.observation.ask
        bid = value.observation.bid
        if state is None or ask is None or bid is None:
            return

        stale_limit = self.capture_config.quote_stale_after_seconds
        freshness = (
            value.quote_freshness_seconds,
            value.last_price_freshness_seconds,
            value.processing_age_seconds,
            value.delivery_age_seconds,
        )
        if (
            not value.halt_state_known
            or not value.volume_known
            or not value.observation.tradable
            or value.observation.halted
            or signal.session not in self.config.entry.allowed_sessions
            or any(age is None or age > stale_limit for age in freshness)
        ):
            return

        def current_gates_valid() -> bool:
            return bool(
                value.halt_state_known
                and value.volume_known
                and value.observation.tradable
                and not value.observation.halted
                and signal.session in self.config.entry.allowed_sessions
                and signal.spread_percent is not None
                and signal.spread_percent <= self.config.entry.maximum_spread_percent
                and signal.dollar_volume >= self.config.entry.minimum_dollar_volume
                and account.risk_engine_approved
                and not account.broker_restriction
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
                    max_displacement_percent=self.config.adaptive_entry.max_displacement_percent,
                    max_displacement_absolute=self.config.adaptive_entry.max_displacement_absolute,
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
            self._paper_entry_rearmer(
                signal, position.shares, position.risk_dollars,
                opportunity_id=opportunity_identity(signal),
                opportunity_anchor=self._paper[signal.symbol].signal.entry_trigger,
                max_lifecycles_per_opportunity=self.config.adaptive_entry.max_lifecycles_per_opportunity,
                max_displacement_percent=self.config.adaptive_entry.max_displacement_percent,
                max_displacement_absolute=self.config.adaptive_entry.max_displacement_absolute,
                now=value.evaluation_timestamp or value.observation.timestamp,
            )
        except Exception:
            return

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
            or candidate.spread_percent is None
            or candidate.spread_percent > self.config.entry.maximum_spread_percent
            or candidate.dollar_volume < self.config.entry.minimum_dollar_volume
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
            if state.last_bar_timestamp is None or bar.timestamp > state.last_bar_timestamp:
                records.extend(self._advance_paper(state, bar, observed_at))
        counter = self._counterfactual.get(normalized)
        if counter is not None:
            if counter.last_bar_timestamp is None or bar.timestamp > counter.last_bar_timestamp:
                counter.bars_observed += 1
                counter.last_bar_timestamp = bar.timestamp
                risk = counter.trigger - counter.stop
                records.append(CaptureRecord.create(
                    CaptureRecordType.COUNTERFACTUAL, normalized, observed_at,
                    {"action": "PATH", "source_bar_timestamp": bar.timestamp,
                     "open": bar.open, "high": bar.high, "low": bar.low,
                     "close": bar.close, "volume": bar.volume,
                     "high_r": None if risk <= 0 else (bar.high - counter.trigger) / risk,
                     "low_r": None if risk <= 0 else (bar.low - counter.trigger) / risk,
                     "bars_observed": counter.bars_observed},
                    identity_parts=(bar.timestamp.isoformat(),),
                ))
                if counter.bars_observed >= self.capture_config.counterfactual_bars:
                    records.append(CaptureRecord.create(
                        CaptureRecordType.COUNTERFACTUAL, normalized, observed_at,
                        {"action": "END", "bars_observed": counter.bars_observed},
                        identity_parts=("END", str(counter.started_at)),
                    ))
                    self._counterfactual.pop(normalized, None)
        if self._shadow is not None:
            records.extend(self._shadow.observe_bar(bar))
        if records:
            self._submit_records(tuple(records))

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
        if prepared:
            self.writer.submit_many(tuple(prepared))

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

    def _profit_defense_action(
        self, state: _PaperState, bar: MinuteBar,
    ) -> tuple[str, Decimal, int] | None:
        config = self.config.trade_management
        if not config.profit_defense_enabled or state.remaining <= 0:
            return None
        self._update_peak(state)
        risk = state.signal.risk_per_share
        if not state.profit_defense_armed or risk <= ZERO or state.peak_r is None:
            return None
        current_r = (bar.close - state.entry_price) / risk
        state.current_r = current_r
        self._update_peak(state)
        giveback = state.peak_r - current_r
        bearish_close = bar.close < bar.open
        lower_than_prior = state.prior_low is not None and bar.close < state.prior_low
        lower_high = state.maximum_high is not None and bar.high < state.maximum_high
        if (
            state.second_taken
            and not state.profit_defense_runner_exit
            and state.peak_r >= config.profit_defense_exit_activation_r
            and giveback >= config.profit_defense_exit_giveback_r
            and current_r > ZERO
            and bearish_close and lower_high
        ):
            return "PROFIT_DEFENSE_RUNNER_EXIT", bar.close, state.remaining
        if (
            not state.profit_defense_stop_tightened
            and state.peak_r >= config.profit_defense_activation_r
            and giveback >= config.profit_defense_tighten_giveback_r
            and current_r > ZERO
            and (bearish_close or lower_than_prior)
        ):
            desired = state.entry_price + (
                state.peak_r - config.profit_defense_tighten_giveback_r
            ) * risk
            desired = min(desired, bar.close)
            floor = max(state.stop, state.entry_price if state.first_taken else state.stop)
            if desired > floor:
                return "PROFIT_DEFENSE_STOP_TIGHTENED", desired, state.remaining
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
            first, second, risk_budget=risk_dollars,
        )
        self._paper[signal.symbol] = state
        fill = CaptureRecord.create(
            CaptureRecordType.PAPER_FILL, signal.symbol, signal.timestamp,
            {"action": "ENTRY", "setup": signal.setup_type.value,
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
            (fill, transition, _management_context_record(
                signal.symbol, signal.timestamp, signal, state,
            )),
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
                    reason, price, quantity = defense
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
        signal = state.signal
        quantity = max(0, int(self._paper_position_quantity_source(signal.symbol)))
        previous = state.remaining if state.authoritative_position_seen else 0
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
            records.append(self._authoritative_exit_record(state, bar, observed_at))
            records.append(_management_context_record(
                signal.symbol, observed_at, signal, state, phase="CLOSED",
            ))
            self._last_transition[signal.symbol] = ForwardTransition.PAPER_EXIT
            self._paper.pop(signal.symbol, None)
            return tuple(records)

        state.authoritative_position_seen = True
        state.remaining = quantity
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

        if self._last_transition.get(signal.symbol) is ForwardTransition.PAPER_EXIT:
            records.append(self._position_contradiction_record(state, observed_at))

        if previous > quantity:
            if state.exit_reason == "FIRST_TARGET":
                state.first_taken = True
                state.stop = max(state.stop, state.entry_price)
            elif state.exit_reason == "SECOND_TARGET":
                state.second_taken = True
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

        requested: tuple[Decimal, int, str] | None = None
        stop_breach = bar.low <= state.stop
        stop_eligible = stop_breach and (
            not protection_activated_this_bar
            and
            state.protective_stop_activated_at is not None
            and state.protective_stop_activated_at <= bar.timestamp
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
        elif state.exit_reason is not None and state.exit_price is not None:
            requested = (state.exit_price, quantity, state.exit_reason)
        elif not state.first_taken and bar.high >= signal.target_levels[0]:
            requested = (signal.target_levels[0], min(state.first_quantity, quantity), "FIRST_TARGET")
        elif not state.second_taken and bar.high >= signal.target_levels[1]:
            requested = (signal.target_levels[1], min(state.second_quantity, quantity), "SECOND_TARGET")
        elif bar.high >= signal.target_levels[2]:
            requested = (signal.target_levels[2], quantity, "RUNNER_TARGET")
        if requested is None:
            requested = self._profit_defense_action(state, bar)

        if requested is not None:
            price, requested_quantity, reason = requested
            if reason != "PROFIT_DEFENSE_STOP_TIGHTENED":
                state.exit_reason = reason
                state.exit_price = price
            result = self._submit_exit(state, price, requested_quantity, reason)
            if reason == "STOP":
                self._capture_stop_activation(state, result, bar)
            active = (
                result.protection_active
                if isinstance(result, PaperExitSubmissionDecision)
                else bool(result)
            )
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
            elif reason == "PROFIT_DEFENSE_RUNNER_EXIT":
                state.profit_defense_runner_exit = True
                state.profit_defense_last_action = reason

        if state.first_taken and state.prior_low is not None and state.prior_low < bar.close:
            state.stop = max(state.stop, state.prior_low)
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
             "authority": "AUTHORITATIVE_POSITION_PROJECTION"},
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

    def _submit_exit(self, state: _PaperState, price: Decimal, quantity: int, reason: str) -> object:
        if self._paper_exit_submitter is not None:
            return self._paper_exit_submitter(
                state.signal.symbol, quantity, price, reason,
                lifecycle_identity(state.signal),
            )
        return False

    def _paper_fill(self, state: _PaperState, timestamp, action: str, label: str,
                    price: Decimal, quantity: int) -> CaptureRecord:
        return CaptureRecord.create(
            CaptureRecordType.PAPER_FILL, state.signal.symbol, timestamp,
            {"action": action, "label": label, "fill_price": price,
             "filled_shares": quantity, "remaining_before": state.remaining,
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
        attributed = tuple(records_with_configuration_fingerprint(self.store.records()))
        if self.paper_campaign_id is not None:
            attributed = tuple(
                (record, fingerprint) for record, fingerprint in attributed
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
        # Fingerprint isolation remains the default.  A prior generation may
        # be resumed only when an immutable Warrior ENTRY fill and an active
        # management context prove the same lifecycle, stop, and target
        # model.  This is an explicit, auditable migration boundary; a bare
        # broker position can never create a Warrior state here.
        current_lifecycles = {
            str(record.payload.get("lifecycle_id") or lifecycle_identity(
                _signal_from_entry(record, record.payload)
            ))
            for record in records
            if record.record_type is CaptureRecordType.PAPER_FILL
            and record.payload.get("action") == "ENTRY"
        }
        entries: dict[str, CaptureRecord] = {}
        contexts: dict[str, CaptureRecord] = {}
        for record, fingerprint in attributed:
            payload = record.payload
            if record.record_type is CaptureRecordType.PAPER_FILL and payload.get("action") == "ENTRY":
                lifecycle = payload.get("lifecycle_id") or lifecycle_identity(
                    _signal_from_entry(record, payload)
                )
                entries[str(lifecycle)] = record
            elif record.record_type is CaptureRecordType.MANAGEMENT_CONTEXT:
                lifecycle = payload.get("lifecycle_id")
                if not lifecycle:
                    continue
                contexts[str(lifecycle)] = record
        for lifecycle, context in contexts.items():
            if lifecycle in current_lifecycles:
                continue
            entry = entries.get(lifecycle)
            if entry is None:
                continue
            context_fingerprint = next(
                fingerprint for record, fingerprint in attributed
                if record.record_id == context.record_id
            )
            if not self._compatible_recovery_context(
                entry, context, context_fingerprint,
            ):
                continue
            symbol_quantity = (
                0 if self._paper_position_quantity_source is None else
                max(0, int(self._paper_position_quantity_source(entry.symbol)))
            )
            if symbol_quantity <= 0:
                continue
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
                signal = _signal_from_entry(record, payload)
                quantity = int(payload["filled_shares"])
                first = int((Decimal(quantity) * self.config.trade_management.first_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
                second = int((Decimal(quantity) * self.config.trade_management.second_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
                self._paper[record.symbol] = _PaperState(
                    signal, Decimal(payload["fill_price"]), quantity, quantity,
                    Decimal(payload["structural_stop"]), first, second,
                )
                self._paper[record.symbol].risk_budget = Decimal(
                    payload.get("risk_dollars", signal.risk_per_share * quantity)
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
                state.protective_stop_activated_at = (
                    None if payload.get("protective_stop_activated_at") is None
                    else datetime.fromisoformat(payload["protective_stop_activated_at"])
                )
                self._last_transition.setdefault(
                    record.symbol, ForwardTransition.PAPER_ENTRY,
                )
            except (KeyError, TypeError, ValueError):
                self._paper.pop(record.symbol, None)

    def _compatible_recovery_context(
        self, entry: CaptureRecord, context: CaptureRecord,
        context_fingerprint: str | None,
    ) -> bool:
        """Permit only structurally proven same-lifecycle migration."""
        if context_fingerprint in (None, self.configuration_fingerprint):
            return False
        entry_payload = entry.payload
        context_payload = context.payload
        entry_lifecycle = entry_payload.get("lifecycle_id") or lifecycle_identity(
            _signal_from_entry(entry, entry_payload)
        )
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
    if stale:
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
    elif ReasonCode.SPREAD_WIDE in candidate.reason_codes:
        result = PaperEntryAuthorizationResult.REFUSED
        reason = PaperEntryAuthorizationReason.SPREAD_WIDE
    else:
        result = PaperEntryAuthorizationResult.REFUSED
        reason = PaperEntryAuthorizationReason.RISK_REJECTED
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
            "setup": signal.setup_type.value,
            "entry_timestamp": signal.timestamp,
            "planned_entry": state.entry_price,
            "structural_stop": signal.stop_price,
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
            "remaining": state.remaining,
            "authoritative_position_seen": state.authoritative_position_seen,
            "exit_reason": state.exit_reason,
            "exit_price": state.exit_price,
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


def _decision_record(value, candidate, completed, features) -> CaptureRecord:
    from .setup_diagnostics import production_setup_diagnostics

    observation = value.observation
    setup = candidate.setup
    payload = {
        "policy_version": candidate.policy_version,
        "discovery_status": "PASSED" if candidate.discovery_qualified else "BLOCKED",
        "entry_status": "READY" if candidate.status is CandidateStatus.ENTRY_READY else "BLOCKED",
        "decision_timestamp": candidate.timestamp,
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
        {"gate": "spread", "passed": candidate.spread_percent is not None and candidate.spread_percent <= config.entry.maximum_spread_percent,
         "observed": candidate.spread_percent, "limit": config.entry.maximum_spread_percent},
        {"gate": "catalyst", "passed": not config.entry.require_catalyst_for_entry or candidate.catalyst_status.value == "TRUE",
         "observed": candidate.catalyst_status.value, "limit": "TRUE"},
        {"gate": "liquidity", "passed": candidate.dollar_volume >= config.entry.minimum_dollar_volume,
         "observed": candidate.dollar_volume, "limit": config.entry.minimum_dollar_volume},
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
    if mode is PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR:
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
    )


__all__ = ["WarriorForwardCaptureService"]
