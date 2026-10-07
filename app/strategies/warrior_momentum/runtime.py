"""Discovery -> ranking -> setup -> signal runtime with a permanent V1 live guard."""

from __future__ import annotations

from dataclasses import replace
from collections import OrderedDict, deque
from datetime import timedelta
from decimal import Decimal

from app.momentum_scanner.models import (
    CatalystStatus, CatalystType, ExecutionQuality, ScannerObservation,
)
from app.momentum_scanner.rules import calculate_metrics
from app.momentum_scanner.quality import (
    momentum_priority, reevaluation_cadence_ms, rvol_quality,
    spread_quality, velocity_attention,
)
from app.market.calendar import EASTERN
from app.performance_diagnostics import performance_diagnostics

from .configuration import AtlasStrategy, StrategySelection, WarriorMomentumConfig
from .discovery import (
    candidate_status, detect_stocks_in_play, discovery_qualified,
    discovery_reasons,
)
from .features import build_features, canonical_completed_history
from .models import (
    STRATEGY_ID, CandidateStatus, MinuteBar, MomentumCandidate, MomentumEntrySignal,
    ReasonCode, SetupState, SetupType, WarriorSetupEvidence,
)
from .scoring import momentum_score
from .setups import (
    AccelerationPoint, LegacySetupEpisodeTracker, detect_best_setup,
    detect_momentum_acceleration, detect_momentum_reacceleration,
)
from .adaptive_context import AdaptiveDecision, WarriorAdaptiveContext
from .detector_diagnostics import BoundedSetupDiagnostics


class WarriorMomentumRuntime:
    def __init__(self, config: WarriorMomentumConfig = WarriorMomentumConfig()) -> None:
        self.config = config
        self._legacy_episode_tracker = LegacySetupEpisodeTracker()
        self._adaptive_context = WarriorAdaptiveContext() if config.adaptive_context_enabled else None
        self._setup_continuity: OrderedDict[str, tuple[object, object, str]] = OrderedDict()
        self._setup_continuity_limit = 512
        self._setup_continuity_age = timedelta(seconds=120)
        self._canonical_candidates: OrderedDict[str, MomentumCandidate] = OrderedDict()
        self._canonical_candidate_limit = 512
        self._acceleration_points: OrderedDict[str, deque[AccelerationPoint]] = OrderedDict()
        self._acceleration_session: dict[str, tuple[object, str]] = {}
        self._acceleration_fingerprints: dict[str, tuple[object, ...]] = {}
        self._acceleration_limit = 512
        # Observation-only, latest-state diagnostics.  This store is bounded
        # independently from candidate/setup state and cannot affect decisions.
        self._detector_diagnostics = BoundedSetupDiagnostics()
        performance_diagnostics.register_detector_diagnostics(self._detector_diagnostics)

    def discover(self, observation: ScannerObservation, bars: tuple[MinuteBar, ...], *, session: str,
                 top_gapper: bool = False) -> MomentumCandidate:
        # Re-register after a performance run reset; publication remains
        # observational and does not participate in candidate decisions.
        performance_diagnostics.register_detector_diagnostics(self._detector_diagnostics)
        normalized_symbol = observation.symbol.strip().upper()
        prior_candidate = self._canonical_candidates.get(normalized_symbol)
        if (
            prior_candidate is not None
            and prior_candidate.timestamp.astimezone(EASTERN).date()
            == observation.timestamp.astimezone(EASTERN).date()
            and observation.timestamp < prior_candidate.timestamp
        ):
            # Older callbacks cannot regress the execution-authoritative
            # setup lifecycle.  The newest canonical result remains visible.
            return prior_candidate
        bars = canonical_completed_history(
            bars, observation.timestamp, session=session,
        )
        metrics = calculate_metrics(observation)
        features = build_features(bars)
        points = self._acceleration_points.get(normalized_symbol)
        session_key = (observation.timestamp.astimezone(EASTERN).date(), session)
        if self._acceleration_session.get(normalized_symbol) != session_key:
            points = deque(maxlen=128)
            self._acceleration_points[normalized_symbol] = points
            self._acceleration_session[normalized_symbol] = session_key
            self._acceleration_fingerprints.pop(normalized_symbol, None)
        point = AccelerationPoint(
            observation.timestamp, observation.price, observation.current_volume,
            metrics.dollar_volume, metrics.spread_percent, observation.tradable,
            observation.halted, True,
        )
        fingerprint = (
            point.timestamp, point.price, point.volume, point.dollar_volume,
            point.spread_percent, point.tradable, point.halted, point.fresh,
        )
        if self._acceleration_fingerprints.get(normalized_symbol) == fingerprint:
            performance_diagnostics.increment("acceleration_point_duplicate_skipped")
        else:
            points.append(point)
            self._acceleration_fingerprints[normalized_symbol] = fingerprint
            performance_diagnostics.increment("acceleration_point_appended")
        self._acceleration_points.move_to_end(normalized_symbol)
        while len(self._acceleration_points) > self._acceleration_limit:
            expired, _ = self._acceleration_points.popitem(last=False)
            self._acceleration_session.pop(expired, None)
            self._acceleration_fingerprints.pop(expired, None)
        setup = detect_best_setup(bars, self.config.setups, tuple(points))
        def point_velocity(seconds: int) -> tuple[Decimal | None, Decimal | None]:
            eligible = tuple(
                item for item in points
                if 0 < (point.timestamp - item.timestamp).total_seconds() <= seconds
            )
            if not eligible:
                return None, None
            anchor = eligible[0]
            elapsed = Decimal(str((point.timestamp - anchor.timestamp).total_seconds())) / Decimal("60")
            if elapsed <= 0 or anchor.price <= 0:
                return None, None
            cents = (point.price - anchor.price) / elapsed
            percent = (point.price - anchor.price) / anchor.price * Decimal("100") / elapsed
            return cents, percent
        cents_1m, percent_1m = point_velocity(60)
        cents_5m, percent_5m = point_velocity(300)
        price_acceleration = (
            None if percent_1m is None or percent_5m is None
            else percent_1m - percent_5m
        )
        self._record_acceleration_diagnostics(tuple(points), setup)
        try:
            self._detector_diagnostics.observe(
                normalized_symbol, bars, tuple(points), session=session,
                timestamp=observation.timestamp, config=self.config.setups,
                selected_setup=setup,
            )
        except Exception:
            # Diagnostics are strictly non-authoritative.
            pass
        prior_setup = self._setup_continuity.get(normalized_symbol)
        if setup is None:
            # A temporary quality/execution miss must not erase a legitimate
            # forming structure.  Continuity is observation-only: FORMING can
            # be projected again, but a previous TRIGGERED setup is never
            # resurrected into order authority without fresh geometry.
            if (
                prior_setup is not None
                and getattr(prior_setup, "setup_type", None) not in {
                    SetupType.MOMENTUM_ACCELERATION,
                    SetupType.MOMENTUM_REACCELERATION,
                    SetupType.RECLAIM_CONTINUATION,
                }
            ):
                prior, seen_at, prior_session = prior_setup
                temporary_quality_miss = (
                    metrics.percentage_change >= self.config.discovery.minimum_percentage_change
                    and (
                        metrics.relative_volume < self.config.discovery.minimum_relative_volume
                        or metrics.dollar_volume < self.config.discovery.minimum_dollar_volume
                        or (
                            metrics.spread_percent is not None
                            and metrics.spread_percent > self.config.discovery.maximum_spread_percent
                        )
                    )
                )
                if (
                    getattr(prior, "state", None) is SetupState.FORMING
                    and prior_session == session
                    and observation.timestamp - seen_at <= self._setup_continuity_age
                    and temporary_quality_miss
                ):
                    setup = prior
                else:
                    self._legacy_episode_tracker.invalidate(observation.symbol)
                    self._setup_continuity.pop(normalized_symbol, None)
            else:
                self._legacy_episode_tracker.invalidate(observation.symbol)
        else:
            setup = self._legacy_episode_tracker.observe(
                observation.symbol, setup, session=session,
            )
            self._setup_continuity[normalized_symbol] = (setup, observation.timestamp, session)
            self._setup_continuity.move_to_end(normalized_symbol)
            while len(self._setup_continuity) > self._setup_continuity_limit:
                self._setup_continuity.popitem(last=False)
        supported_catalyst = (
            observation.catalyst_status is CatalystStatus.TRUE
            and observation.catalyst is not CatalystType.NONE
        ) or (
            observation.catalyst_status is not CatalystStatus.TRUE
            and observation.catalyst is CatalystType.NONE
        )
        catalyst_status = observation.catalyst_status if supported_catalyst else CatalystStatus.UNKNOWN
        catalyst_type = observation.catalyst if supported_catalyst else CatalystType.NONE
        score = momentum_score(
            percentage_change=metrics.percentage_change, relative_volume=metrics.relative_volume,
            acceleration=None if features is None else features.volume_acceleration,
            float_shares=observation.float_shares, dollar_volume=metrics.dollar_volume,
            catalyst_state=catalyst_status,
            setup_quality=None if setup is None else setup.score,
            spread_percent=metrics.spread_percent, weights=self.config.weights,
        )
        rvol_score, participation_quality = rvol_quality(
            metrics.relative_volume if metrics.relative_volume_available else None,
        )
        spread_score, execution_quality, execution_block_reason = spread_quality(
            metrics.spread_percent,
            normal_percent=self.config.entry.maximum_spread_percent,
        )
        velocity_score = velocity_attention(cents_1m, percent_1m)
        priority, priority_components = momentum_priority(
            percentage_change=metrics.percentage_change,
            rvol_score=rvol_score,
            dollar_volume=metrics.dollar_volume,
            velocity_score=velocity_score,
            spread_score=spread_score,
            catalyst_present=catalyst_type is not CatalystType.NONE,
        )
        risk_velocity = None
        if (
            setup is not None and setup.stop_price is not None
            and observation.price > setup.stop_price and cents_1m is not None
        ):
            risk_velocity = cents_1m / (observation.price - setup.stop_price)
        reasons = list(discovery_reasons(observation, metrics, self.config.discovery))
        if catalyst_status is CatalystStatus.FALSE:
            reasons.append(ReasonCode.NO_CATALYST)
        elif catalyst_status in {CatalystStatus.UNKNOWN, CatalystStatus.UNAVAILABLE}:
            reasons.append(ReasonCode.CATALYST_UNKNOWN)
        status = candidate_status(score.total, tuple(reasons), self.config.discovery)
        if setup is not None and setup.state is SetupState.FORMING and status in {CandidateStatus.QUALIFIED, CandidateStatus.NEAR_QUALIFIED}:
            status = CandidateStatus.SETUP_FORMING
        evidence = WarriorSetupEvidence(
            symbol=normalized_symbol,
            session=session,
            evaluation_timestamp=observation.timestamp,
            completed_bar_cutoff=observation.timestamp,
            completed_bar_count=len(bars),
            bar_timestamps=tuple(bar.timestamp for bar in bars),
            detector=None if setup is None else setup.setup_type.value,
            state=(
                SetupState.UNKNOWN if setup is None and len(bars) < 5
                else SetupState.NOT_FORMED if setup is None
                else setup.state
            ),
            trigger=None if setup is None else setup.trigger,
            structural_stop=None if setup is None else setup.stop_price,
            opportunity_id=None if setup is None else setup.taxonomy_opportunity_id,
            structural_invalidation=(
                () if setup is None else setup.reason_codes
            ),
        )
        candidate = MomentumCandidate(
            rank=0, symbol=observation.symbol.strip().upper(), timestamp=observation.timestamp,
            price=observation.price, percentage_change=metrics.percentage_change,
            relative_volume=metrics.relative_volume, float_shares=observation.float_shares,
            volume=observation.current_volume, dollar_volume=metrics.dollar_volume,
            spread_percent=metrics.spread_percent, catalyst_status=catalyst_status,
            catalyst_type=catalyst_type,
            score=score, stocks_in_play=detect_stocks_in_play(
                bars, percentage_change=metrics.percentage_change,
                relative_volume=metrics.relative_volume, top_gapper=top_gapper),
            setup=setup, session=session, status=status, tradable=observation.tradable,
            halted=observation.halted,
            distance_from_hod_percent=None if features is None else features.distance_from_hod_percent,
            reason_codes=tuple(dict.fromkeys(reasons)), explanations=(),
            discovery_qualified=discovery_qualified(tuple(reasons)),
            policy_version=self.config.policy_version,
            bid=observation.bid, ask=observation.ask,
            setup_evidence=evidence,
            observation_eligible=(
                observation.price >= self.config.discovery.minimum_price
                and observation.price <= self.config.discovery.maximum_price
                and observation.tradable
                and not observation.halted
            ),
            observation_blockers=tuple(
                code for code, failed in (
                    (ReasonCode.PRICE_TOO_LOW, observation.price < self.config.discovery.minimum_price),
                    (ReasonCode.PRICE_TOO_HIGH, observation.price > self.config.discovery.maximum_price),
                    (ReasonCode.NOT_TRADABLE, not observation.tradable),
                    (ReasonCode.HALTED, observation.halted),
                ) if failed
            ),
            participation_quality=participation_quality,
            relative_volume_status=(
                "AVAILABLE" if metrics.relative_volume_available
                else "UNAVAILABLE"
            ),
            execution_quality=execution_quality,
            execution_block_reason=execution_block_reason,
            price_velocity_cents_1m=cents_1m,
            price_velocity_percent_1m=percent_1m,
            price_velocity_cents_5m=cents_5m,
            price_velocity_percent_5m=percent_5m,
            price_acceleration_percent=price_acceleration,
            risk_velocity_r_per_minute=risk_velocity,
            momentum_priority=priority,
            momentum_priority_components=priority_components,
            reevaluation_cadence_ms=reevaluation_cadence_ms(
                priority, velocity_score,
            ),
        )
        candidate = replace(candidate, explanations=_explanations(candidate))
        adaptive_reasons: tuple[str, ...] = ()
        if self._adaptive_context is not None:
            adaptive = self._adaptive_context.evaluate(candidate)
            adaptive_reasons = tuple(reason.value for reason in adaptive.reasons)
            # RVOL and ordinary spread are contextual quality evidence when
            # explicitly enabled.  Absolute liquidity, catastrophic spread,
            # validity, halt, and tradability remain hard discovery rails.
            if self._adaptive_context.permits_contextual_discovery(candidate):
                contextual_reasons = tuple(
                    code for code in candidate.reason_codes
                    if code not in {
                        ReasonCode.LIQUIDITY_LOW, ReasonCode.FLOAT_HIGH,
                    }
                )
                contextual_status = candidate_status(
                    candidate.score.total, contextual_reasons, self.config.discovery,
                )
                if setup is not None and setup.state is SetupState.FORMING and contextual_status in {
                    CandidateStatus.QUALIFIED, CandidateStatus.NEAR_QUALIFIED,
                }:
                    contextual_status = CandidateStatus.SETUP_FORMING
                candidate = replace(
                    candidate,
                    status=contextual_status,
                    reason_codes=contextual_reasons,
                    discovery_qualified=discovery_qualified(contextual_reasons),
                )
        candidate = replace(
            candidate,
            explanations=(*_explanations(candidate), *adaptive_reasons),
        )
        self._canonical_candidates[normalized_symbol] = candidate
        self._canonical_candidates.move_to_end(normalized_symbol)
        while len(self._canonical_candidates) > self._canonical_candidate_limit:
            self._canonical_candidates.popitem(last=False)
        return candidate

    @staticmethod
    def _record_acceleration_diagnostics(
        points: tuple[AccelerationPoint, ...], selected_setup: object | None,
    ) -> None:
        """Record bounded detector outcomes without changing setup selection."""
        for name, detector, minimum, lifetime in (
            ("acceleration", detect_momentum_acceleration, 3, 30),
            ("reacceleration", detect_momentum_reacceleration, 5, 45),
        ):
            if len(points) < minimum:
                performance_diagnostics.increment(f"{name}_insufficient_points")
                continue
            if (points[-1].timestamp - points[0].timestamp).total_seconds() > lifetime:
                performance_diagnostics.increment(f"{name}_lifetime_exceeded")
                continue
            detection = detector(points)
            if detection.state in {SetupState.FORMING, SetupState.TRIGGERED}:
                if selected_setup is not None and getattr(selected_setup, "setup_type", None) is not detection.setup_type:
                    performance_diagnostics.increment(f"{name}_masked_by_stronger_setup")
                else:
                    performance_diagnostics.increment(f"{name}_{detection.state.value.lower()}")
            else:
                performance_diagnostics.increment(f"{name}_predicate_failed")

    def rank(self, candidates: tuple[MomentumCandidate, ...], *, limit: int = 25) -> tuple[MomentumCandidate, ...]:
        ordered = sorted(candidates, key=lambda item: (-item.momentum_priority, -item.score.total, -item.relative_volume,
                                                        -item.percentage_change, item.symbol))[:limit]
        return tuple(replace(item, rank=index, explanations=(f"Ranked #{index}", *item.explanations))
                     for index, item in enumerate(ordered, 1))

    def detector_diagnostics(self, symbol: str | None = None) -> dict[str, object]:
        """Return bounded latest detector diagnostics for operator/report use."""
        return self._detector_diagnostics.snapshot(symbol)

    def detector_transitions(self, symbol: str) -> tuple[object, ...]:
        return self._detector_diagnostics.transitions(symbol)

    @property
    def setup_continuity_age(self) -> timedelta:
        """Return the detector's bounded structural-continuity lifetime."""
        return self._setup_continuity_age

    def entry_signal(self, candidate: MomentumCandidate) -> MomentumEntrySignal | None:
        reasons = entry_rejections(candidate, self.config, adaptive_context=self._adaptive_context)
        setup = candidate.setup
        if reasons or setup is None or setup.trigger is None or setup.stop_price is None or setup.stop_model is None:
            return None
        risk = setup.trigger - setup.stop_price
        if risk <= 0 or risk > self.config.entry.maximum_risk_per_share:
            return None
        return _entry_signal_from_candidate(candidate)

    def assess_entry(self, candidate: MomentumCandidate) -> tuple[MomentumCandidate, MomentumEntrySignal | None]:
        """Apply strict entry gates without hiding the discovery candidate."""
        rejections = entry_rejections(candidate, self.config, adaptive_context=self._adaptive_context)
        signal = self.entry_signal(candidate)
        if signal is not None:
            return replace(candidate, status=CandidateStatus.ENTRY_READY), signal
        if candidate.setup is not None and candidate.setup.state is SetupState.FORMING:
            status = CandidateStatus.SETUP_FORMING
        elif any(code in rejections for code in (
            ReasonCode.HALTED, ReasonCode.NOT_TRADABLE,
            ReasonCode.SESSION_NOT_ALLOWED, ReasonCode.STOP_TOO_WIDE,
            ReasonCode.STOP_INVALID,
        )):
            status = CandidateStatus.INELIGIBLE_FOR_EXECUTION
        else:
            status = candidate.status
        return replace(candidate, status=status,
                       reason_codes=tuple(dict.fromkeys((*candidate.reason_codes, *rejections)))), None

    def current_execution_liquidity_ok(
        self, candidate: MomentumCandidate, *, quote_fresh: bool = True,
    ) -> bool:
        """Validate executable quote quality without using turnover as a proxy.

        Adaptive PAPER mode has already assessed participation separately. At
        the final execution boundary we require a valid, fresh, tradable quote
        and execution quality of MARGINAL or better. Poor ordinary spreads are
        a temporary wait, not destruction of the opportunity lifecycle.
        """
        return execution_liquidity_ok(
            candidate,
            self.config,
            quote_fresh=quote_fresh,
            maximum_spread_override=self.execution_spread_limit(candidate),
        )

    def execution_spread_limit(self, candidate: MomentumCandidate) -> Decimal:
        """Upper edge of MARGINAL quality; wider valid quotes wait."""
        return self.config.entry.maximum_spread_percent * Decimal("2")

    def execution_displacement_percent(self, candidate: MomentumCandidate) -> Decimal:
        """Return a bounded, context-aware initial-entry displacement allowance.

        The allowance is diagnostic/contextual only; the forward runtime still
        enforces the resulting envelope and all quote, risk, and authorization
        gates.  Weak candidates retain the configured base allowance.
        """
        base = self.config.adaptive_entry.max_displacement_percent
        hard_outer = Decimal("3.0")
        if self._adaptive_context is None:
            return base
        result = self._adaptive_context.evaluate(candidate)
        if result.decision not in {AdaptiveDecision.FORMING, AdaptiveDecision.READY}:
            return base
        support = max(Decimal("0"), min(Decimal("1"),
                        (result.opportunity_score - self._adaptive_context.ready_score)
                        / max(Decimal("0.01"), Decimal("1") - self._adaptive_context.ready_score)))
        velocity = max(Decimal("0"), min(Decimal("1"),
                        result.participation_velocity / Decimal("1000000")))
        bonus = min(Decimal("1.5"), Decimal("0.75") * support + Decimal("0.75") * velocity)
        return min(hard_outer, base + bonus)

    def technical_entry_signal(self, candidate: MomentumCandidate) -> MomentumEntrySignal | None:
        """Recognize structure without treating momentum score as authority.

        The configured score threshold remains an adaptive preference and
        ranking input. It is not structural evidence, so a triggered setup may
        reach the Opportunity Engine below that preference. All non-score
        prerequisites remain enforced here; authoritative freshness, risk,
        account, and ownership checks remain independent fail-closed boundaries.
        """
        ignored = {ReasonCode.SPREAD_WIDE, ReasonCode.STALE_MARKET_DATA}
        technical = replace(
            candidate, spread_percent=Decimal("0"),
            reason_codes=tuple(code for code in candidate.reason_codes if code not in ignored),
        )
        rejections = entry_rejections(
            technical, self.config, adaptive_context=self._adaptive_context,
            include_momentum_preference=False,
        )
        setup = technical.setup
        if (
            rejections or setup is None or setup.trigger is None
            or setup.stop_price is None or setup.stop_model is None
        ):
            return None
        risk = setup.trigger - setup.stop_price
        if risk <= 0 or risk > self.config.entry.maximum_risk_per_share:
            return None
        return _entry_signal_from_candidate(technical)

    def momentum_preference_is_only_entry_rejection(
        self, candidate: MomentumCandidate,
    ) -> bool:
        """Return true only for the legacy soft-score preemption shape."""
        rejections = entry_rejections(
            candidate, self.config, adaptive_context=self._adaptive_context,
        )
        return bool(rejections) and set(rejections) == {ReasonCode.RISK_REJECTED}

    @staticmethod
    def authorize_live(_signal: MomentumEntrySignal) -> bool:
        return False


def _entry_signal_from_candidate(
    candidate: MomentumCandidate,
) -> MomentumEntrySignal:
    """Build the immutable signal after the caller proves prerequisites."""
    setup = candidate.setup
    if (
        setup is None or setup.trigger is None or setup.stop_price is None
        or setup.stop_model is None
    ):
        raise ValueError("complete setup geometry is required")
    risk = setup.trigger - setup.stop_price
    return MomentumEntrySignal(
        strategy_id=STRATEGY_ID, symbol=candidate.symbol,
        timestamp=candidate.timestamp, session=candidate.session,
        momentum_score=candidate.score.total, setup_type=setup.setup_type,
        entry_trigger=setup.trigger, reference_price=candidate.price,
        stop_price=setup.stop_price, stop_model=setup.stop_model,
        risk_per_share=risk,
        target_levels=(
            setup.trigger + risk, setup.trigger + risk * 2,
            setup.trigger + risk * 3,
        ),
        structural_entry_trigger=setup.trigger,
        structural_stop_price=setup.stop_price,
        catalyst_state=candidate.catalyst_status,
        relative_volume=candidate.relative_volume,
        float_shares=candidate.float_shares,
        spread_percent=candidate.spread_percent,
        volume=candidate.volume, dollar_volume=candidate.dollar_volume,
        setup_score=setup.score, reasoning_codes=(),
        execution_authorized=False,
        taxonomy_strategy_id=setup.taxonomy_strategy_id,
        taxonomy_strategy_memberships=setup.taxonomy_strategy_memberships,
        taxonomy_opportunity_id=setup.taxonomy_opportunity_id,
        taxonomy_opportunity_anchor=setup.taxonomy_opportunity_anchor,
        taxonomy_execution_identity=setup.taxonomy_execution_identity,
        taxonomy_invalidation_reason=setup.taxonomy_invalidation_reason,
        structural_episode_id=setup.structural_episode_id,
        structural_anchor=setup.structural_anchor,
    )


def create_selected_experiment(selection: StrategySelection | None = None,
                               config: WarriorMomentumConfig = WarriorMomentumConfig()) -> WarriorMomentumRuntime | None:
    """Opt-in factory that leaves the existing Atlas path untouched by default."""
    selected = selection or StrategySelection.from_env()
    if selected.selected is AtlasStrategy.WARRIOR_MOMENTUM_V1:
        return WarriorMomentumRuntime(config)
    return None


def warrior_observation_eligible(decision: object, config: WarriorMomentumConfig = WarriorMomentumConfig()) -> bool:
    """Identify scanner results worth retaining for adaptive Warrior observation.

    This is deliberately weaker than scanner qualification and entry
    authorization: only legacy quality failures may be contextualized.  Any
    integrity, tradability, price, halt, or broad momentum failure still stops
    the observation route.
    """
    failed = set(getattr(decision, "technical_failed_rules", ()) or ())
    # These scanner failures are contextual quality inputs for Warrior.  The
    # scanner remains truthful and continues to report them; this gate only
    # decides whether Warrior gets an opportunity to assess the live context.
    quality_only = {
        "relative_volume", "float_verified", "low_float", "dollar_volume", "spread",
    }
    if not failed or not failed.issubset(quality_only):
        return False
    if not bool(getattr(decision, "tradable", False)) or bool(getattr(decision, "halted", True)):
        return False
    price = getattr(decision, "price", None)
    move = getattr(getattr(decision, "metrics", None), "percentage_change", None)
    # Observation is intentionally broader than formal qualification and
    # entry.  Existing scanner score/momentum evidence is the quality signal;
    # the legacy RVOL and turnover misses are precisely what adaptive PAPER
    # observation is allowed to contextualize.  Keep the normal move floor so
    # a weak candidate with identical failed quality rules is not retained.
    if price is None or price <= 0 or move is None or move < config.discovery.minimum_percentage_change:
        return False
    score = Decimal(str(getattr(decision, "score", 0)))
    return (
        score >= config.discovery.near_qualified_score
        or move >= config.discovery.minimum_percentage_change * Decimal("2")
    ) and score >= config.discovery.watch_score


def entry_rejections(candidate: MomentumCandidate, config: WarriorMomentumConfig,
                     *, adaptive_context: WarriorAdaptiveContext | None = None,
                     include_momentum_preference: bool = True) -> tuple[ReasonCode, ...]:
    reasons: list[ReasonCode] = []
    setup = candidate.setup
    discovery_gate_codes = {
        ReasonCode.PRICE_TOO_LOW, ReasonCode.PRICE_TOO_HIGH,
        ReasonCode.CHANGE_TOO_LOW, ReasonCode.FLOAT_HIGH,
        ReasonCode.LIQUIDITY_LOW,
        ReasonCode.HALTED, ReasonCode.NOT_TRADABLE,
    }
    contextual_ok = False
    contextual_liquidity_ok = False
    if adaptive_context is not None:
        contextual_ok = adaptive_context.permits_contextual_quality(candidate)
        contextual_liquidity_ok = contextual_ok
    reasons.extend(code for code in candidate.reason_codes if code in discovery_gate_codes
                   and not (contextual_ok and code in {
                       ReasonCode.RVOL_LOW, ReasonCode.SPREAD_WIDE,
                       ReasonCode.FLOAT_HIGH,
                   }))
    if (
        include_momentum_preference
        and candidate.score.total < config.entry.minimum_momentum_score
    ):
        reasons.append(ReasonCode.RISK_REJECTED)
    if setup is None or setup.state is not SetupState.TRIGGERED or setup.score < config.entry.minimum_setup_score:
        reasons.append(ReasonCode.NO_SETUP)
    if candidate.dollar_volume < config.entry.minimum_dollar_volume and not contextual_liquidity_ok:
        reasons.append(ReasonCode.LIQUIDITY_LOW)
    if config.entry.require_catalyst_for_entry and candidate.catalyst_status is not CatalystStatus.TRUE:
        reasons.append(ReasonCode.NO_CATALYST if candidate.catalyst_status is CatalystStatus.FALSE else ReasonCode.CATALYST_UNKNOWN)
    if candidate.halted:
        reasons.append(ReasonCode.HALTED)
    if not candidate.tradable:
        reasons.append(ReasonCode.NOT_TRADABLE)
    if candidate.session not in config.entry.allowed_sessions:
        reasons.append(ReasonCode.SESSION_NOT_ALLOWED)
    if setup is not None and setup.trigger is not None and setup.stop_price is not None:
        risk = setup.trigger - setup.stop_price
        if risk <= 0:
            reasons.append(ReasonCode.STOP_INVALID)
        elif risk > config.entry.maximum_risk_per_share:
            reasons.append(ReasonCode.STOP_TOO_WIDE)
    if ReasonCode.STALE_MARKET_DATA in candidate.reason_codes:
        reasons.append(ReasonCode.STALE_MARKET_DATA)
    return tuple(dict.fromkeys(reasons))


def execution_liquidity_ok(
    candidate: MomentumCandidate, config: WarriorMomentumConfig, *, quote_fresh: bool = True,
    maximum_spread_override: Decimal | None = None,
) -> bool:
    """Shared final quote-safety predicate for execution and diagnostics."""
    if not quote_fresh or candidate.halted or not candidate.tradable:
        return False
    if candidate.price is None or candidate.price <= 0:
        return False
    if candidate.bid is None or candidate.ask is None:
        return False
    if candidate.bid <= 0 or candidate.ask <= 0 or candidate.ask < candidate.bid:
        return False
    if candidate.spread_percent is None:
        return False
    if (
        not config.adaptive_context_enabled
        and candidate.dollar_volume < config.entry.minimum_dollar_volume
    ):
        return False
    _score, quality, _reason = spread_quality(
        candidate.spread_percent,
        normal_percent=config.entry.maximum_spread_percent,
    )
    return quality in {
        ExecutionQuality.EXCELLENT,
        ExecutionQuality.GOOD,
        ExecutionQuality.MARGINAL,
    }


def _explanations(candidate: MomentumCandidate) -> tuple[str, ...]:
    result = [f"Change {candidate.percentage_change:+.1f}%", f"RVOL {candidate.relative_volume:.1f}x"]
    result.append(f"Participation {candidate.participation_quality.value.lower()}")
    result.append(f"Execution quality {candidate.execution_quality.value.lower()}")
    if candidate.price_velocity_cents_1m is not None:
        result.append(f"Velocity {candidate.price_velocity_cents_1m:+.3f}/min")
    result.append("Float unavailable" if candidate.float_shares is None else f"Float {candidate.float_shares / Decimal('1000000'):.1f}M")
    if candidate.catalyst_status is CatalystStatus.TRUE:
        result.append(f"{candidate.catalyst_type.value.replace('_', ' ').title()} catalyst")
    elif candidate.catalyst_type is CatalystType.NONE and candidate.catalyst_status is CatalystStatus.FALSE:
        result.append("Catalyst none (quality factor, not a Balanced V1 blocker)")
    elif candidate.catalyst_status in {CatalystStatus.UNKNOWN, CatalystStatus.UNAVAILABLE}:
        result.append(f"Catalyst {candidate.catalyst_status.value.lower()}")
    if candidate.distance_from_hod_percent is not None:
        result.append(f"{candidate.distance_from_hod_percent:.1f}% below HOD")
    if candidate.setup is not None:
        result.append(f"{candidate.setup.setup_type.value.replace('_', ' ').title()} {candidate.setup.state.value.lower()}")
    if candidate.execution_block_reason is not None:
        result.append(candidate.execution_block_reason.replace("_", " ").title())
    return tuple(result)


__all__ = ["WarriorMomentumRuntime", "create_selected_experiment", "entry_rejections",
           "execution_liquidity_ok", "warrior_observation_eligible"]
