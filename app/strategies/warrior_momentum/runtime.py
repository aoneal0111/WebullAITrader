"""Discovery -> ranking -> setup -> signal runtime with a permanent V1 live guard."""

from __future__ import annotations

from dataclasses import replace
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal

from app.momentum_scanner.models import CatalystStatus, CatalystType, ScannerObservation
from app.momentum_scanner.rules import calculate_metrics
from app.market.calendar import EASTERN

from .configuration import AtlasStrategy, StrategySelection, WarriorMomentumConfig
from .discovery import (
    candidate_status, detect_stocks_in_play, discovery_qualified,
    discovery_reasons,
)
from .features import build_features, canonical_completed_history
from .models import (
    STRATEGY_ID, CandidateStatus, MinuteBar, MomentumCandidate, MomentumEntrySignal,
    ReasonCode, SetupState, WarriorSetupEvidence,
)
from .scoring import momentum_score
from .setups import LegacySetupEpisodeTracker, detect_best_setup
from .adaptive_context import AdaptiveDecision, WarriorAdaptiveContext


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

    def discover(self, observation: ScannerObservation, bars: tuple[MinuteBar, ...], *, session: str,
                 top_gapper: bool = False) -> MomentumCandidate:
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
        setup = detect_best_setup(bars, self.config.setups)
        prior_setup = self._setup_continuity.get(normalized_symbol)
        if setup is None:
            # A temporary quality/execution miss must not erase a legitimate
            # forming structure.  Continuity is observation-only: FORMING can
            # be projected again, but a previous TRIGGERED setup is never
            # resurrected into order authority without fresh geometry.
            if prior_setup is not None:
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
            observation.catalyst in {CatalystType.EARNINGS, CatalystType.SEC_FILING}
            or (observation.catalyst is CatalystType.NONE and observation.catalyst_status is not CatalystStatus.TRUE)
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
        )
        candidate = replace(candidate, explanations=_explanations(candidate))
        if self._adaptive_context is not None:
            adaptive = self._adaptive_context.evaluate(candidate)
            # RVOL and ordinary spread are contextual quality evidence when
            # explicitly enabled.  Absolute liquidity, catastrophic spread,
            # validity, halt, and tradability remain hard discovery rails.
            if adaptive.decision is not AdaptiveDecision.REJECT:
                contextual_reasons = tuple(
                    code for code in candidate.reason_codes
                    if code not in {ReasonCode.RVOL_LOW, ReasonCode.SPREAD_WIDE, ReasonCode.LIQUIDITY_LOW}
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
        candidate = replace(candidate, explanations=_explanations(candidate))
        self._canonical_candidates[normalized_symbol] = candidate
        self._canonical_candidates.move_to_end(normalized_symbol)
        while len(self._canonical_candidates) > self._canonical_candidate_limit:
            self._canonical_candidates.popitem(last=False)
        return candidate

    def rank(self, candidates: tuple[MomentumCandidate, ...], *, limit: int = 25) -> tuple[MomentumCandidate, ...]:
        ordered = sorted(candidates, key=lambda item: (-item.score.total, -item.relative_volume,
                                                        -item.percentage_change, item.symbol))[:limit]
        return tuple(replace(item, rank=index, explanations=(f"Ranked #{index}", *item.explanations))
                     for index, item in enumerate(ordered, 1))

    def entry_signal(self, candidate: MomentumCandidate) -> MomentumEntrySignal | None:
        reasons = entry_rejections(candidate, self.config, adaptive_context=self._adaptive_context)
        setup = candidate.setup
        if reasons or setup is None or setup.trigger is None or setup.stop_price is None or setup.stop_model is None:
            return None
        risk = setup.trigger - setup.stop_price
        if risk <= 0 or risk > self.config.entry.maximum_risk_per_share:
            return None
        return MomentumEntrySignal(
            strategy_id=STRATEGY_ID, symbol=candidate.symbol, timestamp=candidate.timestamp,
            session=candidate.session, momentum_score=candidate.score.total,
            setup_type=setup.setup_type, entry_trigger=setup.trigger,
            reference_price=candidate.price, stop_price=setup.stop_price,
            stop_model=setup.stop_model, risk_per_share=risk,
            target_levels=(setup.trigger + risk, setup.trigger + risk * 2, setup.trigger + risk * 3),
            structural_entry_trigger=setup.trigger,
            structural_stop_price=setup.stop_price,
            catalyst_state=candidate.catalyst_status, relative_volume=candidate.relative_volume,
            float_shares=candidate.float_shares, spread_percent=candidate.spread_percent,
            volume=candidate.volume, dollar_volume=candidate.dollar_volume,
            setup_score=setup.score, reasoning_codes=(), execution_authorized=False,
            taxonomy_strategy_id=setup.taxonomy_strategy_id,
            taxonomy_strategy_memberships=setup.taxonomy_strategy_memberships,
            taxonomy_opportunity_id=setup.taxonomy_opportunity_id,
            taxonomy_opportunity_anchor=setup.taxonomy_opportunity_anchor,
            taxonomy_execution_identity=setup.taxonomy_execution_identity,
            taxonomy_invalidation_reason=setup.taxonomy_invalidation_reason,
            structural_episode_id=setup.structural_episode_id,
            structural_anchor=setup.structural_anchor,
        )

    def assess_entry(self, candidate: MomentumCandidate) -> tuple[MomentumCandidate, MomentumEntrySignal | None]:
        """Apply strict entry gates without hiding the discovery candidate."""
        rejections = entry_rejections(candidate, self.config, adaptive_context=self._adaptive_context)
        signal = self.entry_signal(candidate)
        if signal is not None:
            return replace(candidate, status=CandidateStatus.ENTRY_READY), signal
        if candidate.setup is not None and candidate.setup.state is SetupState.FORMING:
            status = CandidateStatus.SETUP_FORMING
        elif any(code in rejections for code in (
            ReasonCode.SPREAD_WIDE, ReasonCode.HALTED, ReasonCode.NOT_TRADABLE,
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

        Adaptive PAPER mode has already assessed participation separately.  At
        the final execution boundary we only accept a valid, fresh, tradable
        quote within the existing spread safety policy.  The legacy path keeps
        its exact accumulated-dollar-volume requirement for compatibility.
        """
        return execution_liquidity_ok(candidate, self.config, quote_fresh=quote_fresh)

    def technical_entry_signal(self, candidate: MomentumCandidate) -> MomentumEntrySignal | None:
        """Recognize technical actionability without execution authorization."""
        ignored = {ReasonCode.SPREAD_WIDE, ReasonCode.STALE_MARKET_DATA}
        technical = replace(
            candidate, spread_percent=Decimal("0"),
            reason_codes=tuple(code for code in candidate.reason_codes if code not in ignored),
        )
        return self.entry_signal(technical)

    @staticmethod
    def authorize_live(_signal: MomentumEntrySignal) -> bool:
        return False


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
    quality_only = {"relative_volume", "dollar_volume", "spread"}
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
                     *, adaptive_context: WarriorAdaptiveContext | None = None) -> tuple[ReasonCode, ...]:
    reasons: list[ReasonCode] = []
    setup = candidate.setup
    discovery_gate_codes = {
        ReasonCode.PRICE_TOO_LOW, ReasonCode.PRICE_TOO_HIGH,
        ReasonCode.CHANGE_TOO_LOW, ReasonCode.RVOL_LOW, ReasonCode.FLOAT_HIGH,
        ReasonCode.LIQUIDITY_LOW, ReasonCode.SPREAD_WIDE,
        ReasonCode.HALTED, ReasonCode.NOT_TRADABLE,
    }
    contextual_ok = False
    contextual_liquidity_ok = False
    if adaptive_context is not None:
        contextual_ok = adaptive_context.permits_contextual_rvol_spread(candidate)
        contextual_liquidity_ok = contextual_ok
    reasons.extend(code for code in candidate.reason_codes if code in discovery_gate_codes
                   and not (contextual_ok and code in {ReasonCode.RVOL_LOW, ReasonCode.SPREAD_WIDE}))
    if candidate.score.total < config.entry.minimum_momentum_score:
        reasons.append(ReasonCode.RISK_REJECTED)
    if setup is None or setup.state is not SetupState.TRIGGERED or setup.score < config.entry.minimum_setup_score:
        reasons.append(ReasonCode.NO_SETUP)
    if (candidate.spread_percent is None or candidate.spread_percent > config.entry.maximum_spread_percent) and not contextual_ok:
        reasons.append(ReasonCode.SPREAD_WIDE)
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
) -> bool:
    """Shared final quote-safety predicate for execution and diagnostics."""
    if not config.adaptive_context_enabled:
        return candidate.dollar_volume >= config.entry.minimum_dollar_volume
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
    return candidate.spread_percent <= config.entry.maximum_spread_percent


def _explanations(candidate: MomentumCandidate) -> tuple[str, ...]:
    result = [f"Change {candidate.percentage_change:+.1f}%", f"RVOL {candidate.relative_volume:.1f}x"]
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
    if candidate.spread_percent is None or candidate.spread_percent > Decimal("1"):
        result.append("Spread currently too wide or unavailable for entry")
    return tuple(result)


__all__ = ["WarriorMomentumRuntime", "create_selected_experiment", "entry_rejections",
           "execution_liquidity_ok", "warrior_observation_eligible"]
