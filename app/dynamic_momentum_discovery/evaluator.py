"""Pure point-in-time dynamic-momentum discovery evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256

from .models import (
    BroadMarketSnapshot,
    DynamicMomentumObservation,
    MomentumEvent,
    MomentumFeatures,
    ProductionUniverseComparison,
    ScoreComponent,
)
from .provider import BroadDiscoveryRow


ZERO = Decimal("0")
HUNDRED = Decimal("100")
THOUSAND = Decimal("1000")


@dataclass(frozen=True, slots=True)
class DynamicDiscoveryPolicy:
    """Explainable research thresholds; no production consumer exists."""

    minimum_change_percent: Decimal = Decimal("5")
    price_acceleration_percent: Decimal = Decimal("2")
    volume_acceleration_multiple: Decimal = Decimal("1.5")
    minimum_relative_volume: Decimal = Decimal("2")
    minimum_dollar_volume: Decimal = Decimal("1000000")
    maximum_research_spread_percent: Decimal = Decimal("2")
    minimum_top_of_book_liquidity: Decimal = Decimal("1000")
    promotion_score: int = 4
    quote_stale_after_ms: Decimal = Decimal("5000")
    version: str = "DYNAMIC_MOMENTUM_RESEARCH_V1"

    def __post_init__(self) -> None:
        if self.promotion_score < 1 or min(
            self.minimum_change_percent, self.price_acceleration_percent,
            self.volume_acceleration_multiple, self.minimum_relative_volume,
            self.minimum_dollar_volume, self.maximum_research_spread_percent,
            self.minimum_top_of_book_liquidity, self.quote_stale_after_ms,
        ) <= 0:
            raise ValueError("research thresholds must be positive")


def snapshot_from_rows(
    rows: tuple[BroadDiscoveryRow, ...], *, decision_cutoff: datetime,
    session: str, production_stages: tuple[str, ...] = (),
    bid: Decimal | None = None, ask: Decimal | None = None,
    bid_size: Decimal | None = None, ask_size: Decimal | None = None,
    quote_timestamp: datetime | None = None,
    recent_1m_change_percent: Decimal | None = None,
    recent_5m_change_percent: Decimal | None = None,
    volume_acceleration: Decimal | None = None,
    fresh_high_count: int = 0,
    first_acceleration_at: datetime | None = None,
    prior_session_high: Decimal | None = None,
) -> BroadMarketSnapshot:
    if not rows or len({row.symbol for row in rows}) != 1:
        raise ValueError("one symbol's broad rows are required")
    ordered = tuple(sorted(rows, key=lambda row: (
        row.membership.source.value, row.membership.rank
    )))
    price = _first(ordered, "price")
    if price is None:
        raise ValueError("broad discovery row has no price")
    return BroadMarketSnapshot(
        symbol=ordered[0].symbol,
        decision_cutoff=decision_cutoff,
        session=session,
        memberships=tuple(row.membership for row in ordered),
        price=price,
        previous_close=_first(ordered, "previous_close"),
        open_price=_first(ordered, "open_price"),
        session_high=_first(ordered, "high"),
        prior_session_high=prior_session_high,
        volume=_max(ordered, "volume"),
        relative_volume=_max(ordered, "relative_volume"),
        turnover=_max(ordered, "turnover"),
        bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
        quote_timestamp=quote_timestamp,
        recent_1m_change_percent=recent_1m_change_percent,
        recent_5m_change_percent=recent_5m_change_percent,
        volume_acceleration=volume_acceleration,
        fresh_high_count=fresh_high_count,
        first_acceleration_at=first_acceleration_at,
        production_stages=production_stages,
    )


def evaluate_dynamic_momentum(
    snapshot: BroadMarketSnapshot, *,
    previous: BroadMarketSnapshot | None = None,
    evaluated_at: datetime | None = None,
    policy: DynamicDiscoveryPolicy = DynamicDiscoveryPolicy(),
) -> DynamicMomentumObservation:
    created_at = evaluated_at or snapshot.decision_cutoff
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("evaluation time must be timezone-aware")
    if created_at < snapshot.decision_cutoff:
        raise ValueError("evaluation cannot precede decision cutoff")
    features = _features(snapshot)
    events = _events(snapshot, previous, features, policy)
    components = _components(snapshot, features, events, policy)
    score = sum(item.points for item in components if item.available)
    promotes = score >= policy.promotion_score and bool(events)
    reason = (
        "EXPLAINABLE_RESEARCH_SCORE_AND_MOMENTUM_EVENT"
        if promotes else "RESEARCH_SCORE_OR_EVENT_GATE_NOT_MET"
    )
    episode_material = "|".join((
        policy.version, snapshot.symbol, str(snapshot.decision_cutoff.date()),
        ",".join(event.value for event in events),
    ))
    observation_material = "|".join((
        episode_material, snapshot.decision_cutoff.isoformat(), str(snapshot.price),
        str(snapshot.volume), str(snapshot.relative_volume), str(snapshot.bid),
        str(snapshot.ask), str(score),
    ))
    return DynamicMomentumObservation(
        observation_id=sha256(observation_material.encode()).hexdigest(),
        episode_id=sha256(episode_material.encode()).hexdigest(),
        snapshot=snapshot,
        features=features,
        events=events,
        components=components,
        shadow_score=score,
        shadow_promote_to_full_analysis=promotes,
        promotion_reason=reason,
        production_comparison=production_comparison(snapshot.production_stages),
        created_at=created_at,
    )


def production_comparison(stages: tuple[str, ...]) -> ProductionUniverseComparison:
    values = {value.strip().upper() for value in stages}
    if "SCANNER_EVALUATION_REACHED" in values:
        return ProductionUniverseComparison.PRODUCTION_SCANNER_REACHED
    if "UNIVERSE_ADMITTED" in values:
        return ProductionUniverseComparison.PRODUCTION_ADMITTED
    if "REFERENCE_WARMUP_REJECTED" in values:
        return ProductionUniverseComparison.PRODUCTION_REFERENCE_REJECTED
    if "NORMALIZATION_REJECTED" in values:
        return ProductionUniverseComparison.PRODUCTION_NORMALIZATION_REJECTED
    gainers = bool(values & {"PRODUCTION_RETURNED_GAINERS", "DAY_GAINERS", "PREMARKET_GAINERS"})
    rvol = bool(values & {"PRODUCTION_RETURNED_RVOL", "RELATIVE_VOLUME_10D"})
    if gainers and rvol:
        return ProductionUniverseComparison.PRODUCTION_RETURNED_BOTH
    if gainers:
        return ProductionUniverseComparison.PRODUCTION_RETURNED_GAINERS
    if rvol:
        return ProductionUniverseComparison.PRODUCTION_RETURNED_RVOL
    return ProductionUniverseComparison.PRODUCTION_NOT_RETURNED


def semantic_signature(snapshot: BroadMarketSnapshot) -> str:
    """Exclude wall-clock cutoff so unchanged repeats collapse within a session."""
    material = "|".join((
        snapshot.symbol, str(snapshot.decision_cutoff.date()), snapshot.session,
        str(snapshot.price), str(snapshot.open_price), str(snapshot.session_high),
        str(snapshot.volume),
        str(snapshot.relative_volume), str(snapshot.bid), str(snapshot.ask),
        str(snapshot.bid_size), str(snapshot.ask_size),
        str(snapshot.recent_1m_change_percent),
        str(snapshot.recent_5m_change_percent), str(snapshot.volume_acceleration),
        str(snapshot.fresh_high_count),
        ",".join(f"{item.source.value}:{item.rank}" for item in snapshot.memberships),
        ",".join(snapshot.production_stages),
    ))
    return sha256(material.encode()).hexdigest()


def _features(snapshot):
    change = _percent(snapshot.price - snapshot.previous_close, snapshot.previous_close)
    gap = _percent(
        snapshot.open_price - snapshot.previous_close
        if snapshot.open_price is not None and snapshot.previous_close is not None
        else None,
        snapshot.previous_close,
    )
    dollar_volume = (
        snapshot.price * snapshot.volume if snapshot.volume is not None else None
    )
    distance_high = (
        _percent(snapshot.price - snapshot.session_high, snapshot.session_high)
        if snapshot.session_high not in (None, ZERO) else None
    )
    spread = (
        snapshot.ask - snapshot.bid
        if snapshot.ask is not None and snapshot.bid is not None else None
    )
    spread_percent = (
        spread / snapshot.ask * HUNDRED
        if spread is not None and snapshot.ask not in (None, ZERO) else None
    )
    liquidity = (
        snapshot.bid_size + snapshot.ask_size
        if snapshot.bid_size is not None and snapshot.ask_size is not None else None
    )
    quote_age = (
        Decimal(str((snapshot.decision_cutoff - snapshot.quote_timestamp).total_seconds()))
        * THOUSAND if snapshot.quote_timestamp is not None else None
    )
    persistence = (
        int((snapshot.decision_cutoff - snapshot.first_acceleration_at).total_seconds())
        if snapshot.first_acceleration_at is not None else None
    )
    return MomentumFeatures(
        change_percent=change, gap_percent=gap, dollar_volume=dollar_volume,
        distance_from_high_percent=distance_high, spread=spread,
        spread_percent=spread_percent, top_of_book_liquidity=liquidity,
        quote_age_ms=quote_age, momentum_persistence_seconds=persistence,
    )


def _events(snapshot, previous, features, policy):
    values: set[MomentumEvent] = set()
    if snapshot.volume_acceleration is not None and snapshot.volume_acceleration >= policy.volume_acceleration_multiple:
        values.add(MomentumEvent.ABNORMAL_VOLUME_ACCELERATION)
    if snapshot.recent_1m_change_percent is not None and snapshot.recent_1m_change_percent >= policy.price_acceleration_percent:
        values.add(MomentumEvent.PRICE_ACCELERATION)
    if snapshot.prior_session_high is not None and snapshot.price > snapshot.prior_session_high:
        values.add(
            MomentumEvent.PREMARKET_BREAKOUT
            if snapshot.session in {"PREMARKET", "PRE_MARKET"}
            else MomentumEvent.SESSION_HIGH_BREAKOUT
        )
    if features.gap_percent is not None and features.gap_percent >= policy.minimum_change_percent:
        values.add(MomentumEvent.GAP_EXPANSION)
    if (
        features.spread_percent is not None
        and features.spread_percent <= policy.maximum_research_spread_percent
        and features.top_of_book_liquidity is not None
        and features.top_of_book_liquidity >= policy.minimum_top_of_book_liquidity
    ):
        values.add(MomentumEvent.LIQUIDITY_EMERGENCE)
    if (
        snapshot.fresh_high_count >= 3
        or snapshot.recent_5m_change_percent is not None
        and snapshot.recent_5m_change_percent >= policy.minimum_change_percent
    ):
        values.add(MomentumEvent.MOMENTUM_PERSISTENCE)
    if (
        previous is not None
        and previous.recent_1m_change_percent is not None
        and snapshot.recent_1m_change_percent is not None
        and snapshot.recent_1m_change_percent > previous.recent_1m_change_percent > ZERO
    ):
        values.add(MomentumEvent.MOMENTUM_REACCELERATION)
    return tuple(sorted(values, key=lambda item: item.value))


def _components(snapshot, features, events, policy):
    def component(name, condition, available, reason):
        return ScoreComponent(name, 1 if available and condition else 0, available, reason)
    return (
        component("PRICE_CHANGE", features.change_percent is not None and features.change_percent >= policy.minimum_change_percent, features.change_percent is not None, "change versus captured previous close"),
        component("RELATIVE_VOLUME", snapshot.relative_volume is not None and snapshot.relative_volume >= policy.minimum_relative_volume, snapshot.relative_volume is not None, "captured Webull 10-day relative volume"),
        component("DOLLAR_VOLUME", features.dollar_volume is not None and features.dollar_volume >= policy.minimum_dollar_volume, features.dollar_volume is not None, "price multiplied by captured volume"),
        component("PRICE_ACCELERATION", MomentumEvent.PRICE_ACCELERATION in events, snapshot.recent_1m_change_percent is not None, "point-in-time one-minute change"),
        component("VOLUME_ACCELERATION", MomentumEvent.ABNORMAL_VOLUME_ACCELERATION in events, snapshot.volume_acceleration is not None, "point-in-time volume acceleration"),
        component("NEW_HIGH", bool(set(events) & {MomentumEvent.PREMARKET_BREAKOUT, MomentumEvent.SESSION_HIGH_BREAKOUT}), snapshot.prior_session_high is not None, "price versus prior known session high"),
        component("LIQUIDITY", MomentumEvent.LIQUIDITY_EMERGENCE in events, features.spread_percent is not None and features.top_of_book_liquidity is not None, "L1 spread and displayed top-of-book size"),
        component("PERSISTENCE", MomentumEvent.MOMENTUM_PERSISTENCE in events, snapshot.recent_5m_change_percent is not None or snapshot.fresh_high_count > 0, "five-minute change or repeated fresh highs"),
        component("SOURCE_AGREEMENT", len(snapshot.memberships) >= 2, True, "membership in multiple independent broad screener lists"),
    )


def _percent(numerator, denominator):
    if numerator is None or denominator in (None, ZERO):
        return None
    return numerator / denominator * HUNDRED


def _first(rows, name):
    return next((getattr(row, name) for row in rows if getattr(row, name) is not None), None)


def _max(rows, name):
    values = [getattr(row, name) for row in rows if getattr(row, name) is not None]
    return max(values) if values else None


__all__ = [
    "DynamicDiscoveryPolicy", "evaluate_dynamic_momentum", "production_comparison",
    "semantic_signature", "snapshot_from_rows",
]
