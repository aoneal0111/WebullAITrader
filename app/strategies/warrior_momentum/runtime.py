"""Discovery -> ranking -> setup -> signal runtime with a permanent V1 live guard."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from app.momentum_scanner.models import CatalystStatus, CatalystType, ScannerObservation
from app.momentum_scanner.rules import calculate_metrics

from .configuration import AtlasStrategy, StrategySelection, WarriorMomentumConfig
from .discovery import (
    candidate_status, detect_stocks_in_play, discovery_qualified,
    discovery_reasons,
)
from .features import build_features, completed_bars_as_of
from .models import (
    STRATEGY_ID, CandidateStatus, MinuteBar, MomentumCandidate, MomentumEntrySignal,
    ReasonCode, SetupState,
)
from .scoring import momentum_score
from .setups import detect_best_setup


class WarriorMomentumRuntime:
    def __init__(self, config: WarriorMomentumConfig = WarriorMomentumConfig()) -> None:
        self.config = config

    def discover(self, observation: ScannerObservation, bars: tuple[MinuteBar, ...], *, session: str,
                 top_gapper: bool = False) -> MomentumCandidate:
        bars = completed_bars_as_of(bars, observation.timestamp)
        metrics = calculate_metrics(observation)
        features = build_features(bars)
        setup = detect_best_setup(bars, self.config.setups)
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
        )
        return replace(candidate, explanations=_explanations(candidate))

    def rank(self, candidates: tuple[MomentumCandidate, ...], *, limit: int = 25) -> tuple[MomentumCandidate, ...]:
        ordered = sorted(candidates, key=lambda item: (-item.score.total, -item.relative_volume,
                                                        -item.percentage_change, item.symbol))[:limit]
        return tuple(replace(item, rank=index, explanations=(f"Ranked #{index}", *item.explanations))
                     for index, item in enumerate(ordered, 1))

    def entry_signal(self, candidate: MomentumCandidate) -> MomentumEntrySignal | None:
        reasons = entry_rejections(candidate, self.config)
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
            catalyst_state=candidate.catalyst_status, relative_volume=candidate.relative_volume,
            float_shares=candidate.float_shares, spread_percent=candidate.spread_percent,
            volume=candidate.volume, dollar_volume=candidate.dollar_volume,
            setup_score=setup.score, reasoning_codes=(), execution_authorized=False,
        )

    def assess_entry(self, candidate: MomentumCandidate) -> tuple[MomentumCandidate, MomentumEntrySignal | None]:
        """Apply strict entry gates without hiding the discovery candidate."""
        rejections = entry_rejections(candidate, self.config)
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


def entry_rejections(candidate: MomentumCandidate, config: WarriorMomentumConfig) -> tuple[ReasonCode, ...]:
    reasons: list[ReasonCode] = []
    setup = candidate.setup
    discovery_gate_codes = {
        ReasonCode.PRICE_TOO_LOW, ReasonCode.PRICE_TOO_HIGH,
        ReasonCode.CHANGE_TOO_LOW, ReasonCode.RVOL_LOW, ReasonCode.FLOAT_HIGH,
        ReasonCode.LIQUIDITY_LOW, ReasonCode.SPREAD_WIDE,
        ReasonCode.HALTED, ReasonCode.NOT_TRADABLE,
    }
    reasons.extend(code for code in candidate.reason_codes if code in discovery_gate_codes)
    if candidate.score.total < config.entry.minimum_momentum_score:
        reasons.append(ReasonCode.RISK_REJECTED)
    if setup is None or setup.state is not SetupState.TRIGGERED or setup.score < config.entry.minimum_setup_score:
        reasons.append(ReasonCode.NO_SETUP)
    if candidate.spread_percent is None or candidate.spread_percent > config.entry.maximum_spread_percent:
        reasons.append(ReasonCode.SPREAD_WIDE)
    if candidate.dollar_volume < config.entry.minimum_dollar_volume:
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


__all__ = ["WarriorMomentumRuntime", "create_selected_experiment", "entry_rejections"]
