"""Pure adapter from authoritative Warrior DTOs into research snapshots.

No production runtime imports this module. Integration may call it at meaningful
qualification/setup transitions while retaining one caller-owned episode ID.
"""

from __future__ import annotations

from decimal import Decimal

from app.strategies.warrior_momentum.forward_models import PointInTimeObservation
from app.strategies.warrior_momentum.models import (
    CandidateStatus, FeatureSnapshot, MomentumCandidate, SetupState,
)

from .models import (
    FEATURE_VERSION, AtlasDecision, DecisionTimeSnapshot, OpportunityKey,
    TradeOpportunityExperience,
)


def from_warrior_candidate(
    value: PointInTimeObservation,
    candidate: MomentumCandidate,
    *,
    episode_id: str,
    source_event_identity: str,
    environment: str,
    strategy_version: str,
    model_version: str,
    features: FeatureSnapshot | None = None,
    actually_traded: bool = False,
) -> TradeOpportunityExperience:
    if value.observation.symbol.upper() != candidate.symbol.upper():
        raise ValueError("candidate and observation symbols differ")
    setup = candidate.setup
    trigger = None if setup is None else setup.trigger
    stop = None if setup is None else setup.stop_price
    risk = None if trigger is None or stop is None or trigger <= stop else trigger - stop
    decision = _decision(candidate)
    feature_values = () if features is None else (
        ("distance_from_hod_percent", features.distance_from_hod_percent),
        ("distance_from_vwap_percent", features.distance_from_vwap_percent),
        ("pullback_depth_percent", features.pullback_depth_percent),
        ("consolidation_duration_bars", features.consolidation_duration),
        ("volume_acceleration_ratio", features.volume_acceleration),
        ("breakout_volume_expansion_ratio", features.breakout_volume_ratio),
    )
    feature_sources = () if features is None else tuple(
        (name, features.timestamp) for name, item in feature_values if item is not None
    )
    observation = value.observation
    return TradeOpportunityExperience(
        key=OpportunityKey(
            "WARRIOR_MOMENTUM_V1", candidate.symbol,
            candidate.timestamp.date(), candidate.session, episode_id,
        ),
        environment=environment, policy_version=candidate.policy_version,
        strategy_version=strategy_version, model_version=model_version,
        feature_version=FEATURE_VERSION, source_event_identity=source_event_identity,
        snapshot=DecisionTimeSnapshot(
            decision_timestamp=candidate.timestamp,
            source_timestamp=observation.timestamp,
            last_price=candidate.price, bid=observation.bid, ask=observation.ask,
            spread_percent=candidate.spread_percent,
            percentage_change=candidate.percentage_change,
            current_volume=candidate.volume,
            average_volume=observation.average_30_day_volume,
            relative_volume=candidate.relative_volume,
            dollar_volume=candidate.dollar_volume,
            float_shares=candidate.float_shares,
            tradable=candidate.tradable, halted=candidate.halted,
            quote_freshness_seconds=value.quote_freshness_seconds,
            trade_freshness_seconds=value.last_price_freshness_seconds,
            catalyst_status=candidate.catalyst_status.value,
            catalyst_type=candidate.catalyst_type.value,
            catalyst_source_identity=observation.catalyst_source_url or observation.catalyst_source,
            scanner_qualified=candidate.discovery_qualified,
            scanner_score=None if value.scanner_score is None else Decimal(value.scanner_score),
            scanner_rank=value.scanner_rank,
            failed_rules=value.scanner_failed_rules,
            setup_state=None if setup is None else setup.state.value,
            setup_type=None if setup is None else setup.setup_type.value,
            setup_quality=None if setup is None else setup.score,
            trigger_price=trigger, structural_stop=stop,
            reference_price=trigger or candidate.price, risk_per_share=risk,
            setup_timestamp=None if setup is None else candidate.timestamp,
            completed_bar_identity=None if not value.bars else value.bars[-1].timestamp.isoformat(),
            features=feature_values, feature_source_timestamps=feature_sources,
        ),
        atlas_decision=decision,
        blockers=tuple(dict.fromkeys(item.value for item in candidate.reason_codes)),
        technically_actionable=bool(
            setup is not None and setup.state is SetupState.TRIGGERED and risk is not None
        ),
        actually_traded=actually_traded,
    )


def _decision(candidate: MomentumCandidate) -> AtlasDecision:
    mapping = {
        CandidateStatus.ENTRY_READY: AtlasDecision.ENTRY_READY,
        CandidateStatus.AWAITING_EXECUTION_DATA: AtlasDecision.AWAITING_EXECUTION_DATA,
        CandidateStatus.SETUP_FORMING: AtlasDecision.FORMING,
        CandidateStatus.INELIGIBLE_FOR_EXECUTION: AtlasDecision.REJECTED,
    }
    if candidate.status in mapping:
        return mapping[candidate.status]
    if candidate.setup is None or candidate.setup.state is SetupState.NOT_FORMED:
        return AtlasDecision.NO_SETUP
    if candidate.setup.state is SetupState.TRIGGERED:
        return AtlasDecision.TRIGGERED
    return AtlasDecision.WATCHING
