"""Pure, non-executable research model for post-gap flush/reclaim patterns.

This module is intentionally absent from production setup selection.  It can
describe and score historical geometry, but it cannot authorize or submit an
order.  Future bars are accepted only by ``evaluate_frozen_plan`` after the
structural plan has been frozen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from .features import aligned_bars
from .models import MinuteBar

HUNDRED = Decimal("100")
ONE_MINUTE = timedelta(minutes=1)


class PostGapReclaimState(StrEnum):
    INSUFFICIENT_CONTIGUOUS_BARS = "INSUFFICIENT_CONTIGUOUS_BARS"
    SETUP_GEOMETRY_INVALID = "SETUP_GEOMETRY_INVALID"
    SETUP_FORMING = "SETUP_FORMING"
    SETUP_TRIGGERED = "SETUP_TRIGGERED"
    TRIGGER_ALREADY_CROSSED = "TRIGGER_ALREADY_CROSSED"


class ResearchOutcomeState(StrEnum):
    NO_ENTRY = "NO_ENTRY"
    OPEN = "OPEN"
    STOPPED = "STOPPED"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True, slots=True)
class PostGapResearchConfig:
    """Independent research bounds; none are production Warrior policy."""

    minimum_post_gap_bars: int = 3
    minimum_flush_percent: Decimal = Decimal("3")
    breakout_buffer_percent: Decimal = Decimal("0.05")
    maximum_structural_risk_percent: Decimal = Decimal("3")
    minimum_percentage_change: Decimal = Decimal("5")
    minimum_relative_volume: Decimal = Decimal("2")
    minimum_dollar_volume: Decimal = Decimal("1000000")
    maximum_spread_percent: Decimal = Decimal("1.50")
    maximum_float: Decimal = Decimal("50000000")
    maximum_distance_from_hod_percent: Decimal = Decimal("15")
    pre_gap_reference_bars: int = 5

    def __post_init__(self) -> None:
        if self.minimum_post_gap_bars != 3:
            raise ValueError("post-gap geometry is defined by exactly three seed bars")
        if self.pre_gap_reference_bars <= 0:
            raise ValueError("pre-gap reference window must be positive")
        if any(
            value < 0
            for value in (
                self.minimum_flush_percent,
                self.breakout_buffer_percent,
                self.maximum_structural_risk_percent,
                self.minimum_percentage_change,
                self.minimum_relative_volume,
                self.minimum_dollar_volume,
                self.maximum_spread_percent,
                self.maximum_float,
                self.maximum_distance_from_hod_percent,
            )
        ):
            raise ValueError("research bounds cannot be negative")


@dataclass(frozen=True, slots=True)
class PostGapCandidateContext:
    momentum_qualified: bool
    percentage_change: Decimal
    relative_volume: Decimal
    dollar_volume: Decimal
    spread_percent: Decimal | None
    float_shares: Decimal | None
    distance_from_hod_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class ResearchRule:
    rule: str
    required: str
    actual: str
    passed: bool


@dataclass(frozen=True, slots=True)
class PostGapResearchPlan:
    symbol: str
    setup_name: str
    created_after_bar: datetime
    discontinuity_start: datetime
    trigger: Decimal
    stop: Decimal
    risk_per_share: Decimal
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if self.setup_name != "POST_GAP_RECLAIM":
            raise ValueError("unexpected research setup")
        if self.trigger <= self.stop or self.risk_per_share != self.trigger - self.stop:
            raise ValueError("research plan requires a valid structural risk distance")
        if self.execution_authorized:
            raise ValueError("research plans cannot authorize execution")


@dataclass(frozen=True, slots=True)
class PostGapReclaimDetection:
    state: PostGapReclaimState
    evaluated_post_gap_bars: int
    rules: tuple[ResearchRule, ...]
    plan: PostGapResearchPlan | None = None
    flush_percent: Decimal | None = None
    reclaim_volume_ratio: Decimal | None = None
    reason: str = ""
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if self.execution_authorized:
            raise ValueError("research detections cannot authorize execution")


@dataclass(frozen=True, slots=True)
class FrozenPlanOutcome:
    state: ResearchOutcomeState
    entry_time: datetime | None
    stop_time: datetime | None
    mfe: Decimal | None
    mae: Decimal | None
    mfe_r: Decimal | None
    mae_r: Decimal | None
    reached_1r: bool
    reached_2r: bool
    reached_3r: bool
    conservative_stop_first: bool


def _rule(name: str, required: str, actual: object, passed: bool) -> ResearchRule:
    return ResearchRule(name, required, str(actual), passed)


def _latest_gap(
    bars: tuple[MinuteBar, ...],
) -> tuple[tuple[MinuteBar, ...], tuple[MinuteBar, ...]] | None:
    ordered = aligned_bars(bars)
    gap_index = None
    for index in range(1, len(ordered)):
        if ordered[index].timestamp - ordered[index - 1].timestamp != ONE_MINUTE:
            gap_index = index
    if gap_index is None:
        return None
    return ordered[:gap_index], ordered[gap_index:]


def detect_post_gap_reclaim(
    bars: tuple[MinuteBar, ...],
    context: PostGapCandidateContext,
    config: PostGapResearchConfig = PostGapResearchConfig(),
) -> PostGapReclaimDetection:
    """Evaluate only information present in ``bars`` and freeze seed geometry.

    The first post-gap bar is the flush.  Bars two and three must show range
    and volume contraction plus an equal/higher support low.  Their highs set
    resistance; their lows set the structural stop.  Later bars may change the
    state, but never the frozen trigger or stop.
    """

    split = _latest_gap(bars)
    if split is None:
        return PostGapReclaimDetection(
            PostGapReclaimState.SETUP_GEOMETRY_INVALID,
            0,
            (_rule("discontinuity", "> 1 minute", "none", False),),
            reason="No timestamp discontinuity is present.",
        )
    pre_gap, post_gap = split
    gap_minutes = int((post_gap[0].timestamp - pre_gap[-1].timestamp).total_seconds() / 60)
    if len(post_gap) < config.minimum_post_gap_bars:
        return PostGapReclaimDetection(
            PostGapReclaimState.INSUFFICIENT_CONTIGUOUS_BARS,
            len(post_gap),
            (
                _rule("discontinuity", "> 1 minute", gap_minutes, gap_minutes > 1),
                _rule(
                    "completed_post_gap_bars",
                    f">= {config.minimum_post_gap_bars}",
                    len(post_gap),
                    False,
                ),
            ),
            reason="Three completed post-gap bars are required to freeze support and resistance.",
        )

    flush, stabilization, support = post_gap[:3]
    reference = max(
        bar.high for bar in pre_gap[-config.pre_gap_reference_bars :]
    )
    flush_low = min(flush.low, stabilization.low)
    flush_percent = (reference - flush_low) / reference * HUNDRED
    flush_range = flush.high - flush.low
    stabilization_range = stabilization.high - stabilization.low
    support_range = support.high - support.low
    resistance = max(stabilization.high, support.high)
    trigger = resistance * (Decimal("1") + config.breakout_buffer_percent / HUNDRED)
    stop = min(stabilization.low, support.low)
    risk = trigger - stop
    risk_percent = risk / trigger * HUNDRED

    structural_rules = (
        _rule("discontinuity", "> 1 minute", gap_minutes, gap_minutes > 1),
        _rule("completed_post_gap_bars", ">= 3", len(post_gap), True),
        _rule("momentum_qualified", "true", context.momentum_qualified, context.momentum_qualified),
        _rule(
            "percentage_change",
            f">= {config.minimum_percentage_change}%",
            f"{context.percentage_change}%",
            context.percentage_change >= config.minimum_percentage_change,
        ),
        _rule(
            "relative_volume",
            f">= {config.minimum_relative_volume}",
            context.relative_volume,
            context.relative_volume >= config.minimum_relative_volume,
        ),
        _rule(
            "dollar_volume",
            f">= {config.minimum_dollar_volume}",
            context.dollar_volume,
            context.dollar_volume >= config.minimum_dollar_volume,
        ),
        _rule(
            "spread_percent",
            f"<= {config.maximum_spread_percent}%",
            context.spread_percent,
            context.spread_percent is not None
            and context.spread_percent <= config.maximum_spread_percent,
        ),
        _rule(
            "float_shares",
            f"<= {config.maximum_float}",
            context.float_shares,
            context.float_shares is not None and context.float_shares <= config.maximum_float,
        ),
        _rule(
            "distance_from_hod",
            f"<= {config.maximum_distance_from_hod_percent}%",
            context.distance_from_hod_percent,
            context.distance_from_hod_percent is not None
            and context.distance_from_hod_percent <= config.maximum_distance_from_hod_percent,
        ),
        _rule("flush_candle", "close < open", f"{flush.close} < {flush.open}", flush.close < flush.open),
        _rule(
            "flush_magnitude",
            f">= {config.minimum_flush_percent}%",
            f"{flush_percent}%",
            flush_percent >= config.minimum_flush_percent,
        ),
        _rule(
            "range_contraction",
            "bars 2 and 3 ranges < flush range",
            f"{stabilization_range}, {support_range} < {flush_range}",
            stabilization_range < flush_range and support_range < flush_range,
        ),
        _rule(
            "selling_volume_contraction",
            "bar3 volume <= bar2 volume < flush volume",
            f"{support.volume} <= {stabilization.volume} < {flush.volume}",
            support.volume <= stabilization.volume < flush.volume,
        ),
        _rule(
            "support_low",
            "bar3 low >= bar2 low",
            f"{support.low} >= {stabilization.low}",
            support.low >= stabilization.low,
        ),
        _rule(
            "support_close",
            "bar3 close > support low",
            f"{support.close} > {stop}",
            support.close > stop,
        ),
        _rule(
            "structural_risk",
            f"> 0 and <= {config.maximum_structural_risk_percent}%",
            f"{risk_percent}%",
            risk > 0 and risk_percent <= config.maximum_structural_risk_percent,
        ),
    )
    if not all(item.passed for item in structural_rules):
        return PostGapReclaimDetection(
            PostGapReclaimState.SETUP_GEOMETRY_INVALID,
            len(post_gap),
            structural_rules,
            flush_percent=flush_percent,
            reason="One or more structural or candidate-quality rules failed.",
        )

    plan = PostGapResearchPlan(
        symbol=post_gap[0].symbol.strip().upper(),
        setup_name="POST_GAP_RECLAIM",
        created_after_bar=support.timestamp,
        discontinuity_start=post_gap[0].timestamp,
        trigger=trigger,
        stop=stop,
        risk_per_share=risk,
    )
    reclaim_volume_ratio = None
    if len(post_gap) > 3:
        seed_average = (stabilization.volume + support.volume) / Decimal("2")
        reclaim_volume_ratio = (
            post_gap[-1].volume / seed_average if seed_average > 0 else None
        )

    closes = tuple(bar.close for bar in post_gap[3:])
    if not closes or closes[-1] < trigger:
        state = PostGapReclaimState.SETUP_FORMING
        reason = "Seed geometry is valid; a completed close has not reclaimed resistance."
    elif len(closes) >= 2 and any(close >= trigger for close in closes[:-1]):
        state = PostGapReclaimState.TRIGGER_ALREADY_CROSSED
        reason = "The close-confirmed trigger crossed before this evaluation latency."
    else:
        state = PostGapReclaimState.SETUP_TRIGGERED
        reason = "The latest completed bar is the first close-confirmed reclaim."

    return PostGapReclaimDetection(
        state,
        len(post_gap),
        structural_rules,
        plan,
        flush_percent,
        reclaim_volume_ratio,
        reason,
    )


def evaluate_frozen_plan(
    detection: PostGapReclaimDetection,
    future_bars: tuple[MinuteBar, ...],
) -> FrozenPlanOutcome:
    """Evaluate a frozen plan with stop-first ordering on ambiguous bars."""

    plan = detection.plan
    if plan is None or detection.state is PostGapReclaimState.TRIGGER_ALREADY_CROSSED:
        return _empty_outcome()
    ordered = aligned_bars(future_bars)
    if any(bar.timestamp <= plan.created_after_bar for bar in ordered):
        raise ValueError("outcome bars must be strictly later than plan construction")

    entered = False
    entry_time = None
    stop_time = None
    maximum = plan.trigger
    minimum = plan.trigger
    reached = [False, False, False]
    conservative = False

    for bar in ordered:
        if not entered:
            if bar.close < plan.trigger:
                continue
            entered = True
            entry_time = bar.timestamp

        # OHLC cannot establish whether the stop or target occurred first.
        # Count the stop first and do not credit same-bar reward excursions.
        if bar.low <= plan.stop:
            stop_time = bar.timestamp
            conservative = bar.high >= plan.trigger
            minimum = min(minimum, plan.stop)
            break

        maximum = max(maximum, bar.high)
        minimum = min(minimum, bar.low)
        for index, multiple in enumerate((Decimal("1"), Decimal("2"), Decimal("3"))):
            if bar.high >= plan.trigger + plan.risk_per_share * multiple:
                reached[index] = True

    if not entered:
        return _empty_outcome()
    mfe = maximum - plan.trigger
    mae = plan.trigger - minimum
    state = ResearchOutcomeState.STOPPED if stop_time is not None else (
        ResearchOutcomeState.RESOLVED if reached[2] else ResearchOutcomeState.OPEN
    )
    return FrozenPlanOutcome(
        state,
        entry_time,
        stop_time,
        mfe,
        mae,
        mfe / plan.risk_per_share,
        mae / plan.risk_per_share,
        reached[0],
        reached[1],
        reached[2],
        conservative,
    )


def _empty_outcome() -> FrozenPlanOutcome:
    return FrozenPlanOutcome(
        ResearchOutcomeState.NO_ENTRY,
        None,
        None,
        None,
        None,
        None,
        None,
        False,
        False,
        False,
        False,
    )


__all__ = [
    "FrozenPlanOutcome",
    "PostGapCandidateContext",
    "PostGapReclaimDetection",
    "PostGapReclaimState",
    "PostGapResearchConfig",
    "PostGapResearchPlan",
    "ResearchOutcomeState",
    "ResearchRule",
    "detect_post_gap_reclaim",
    "evaluate_frozen_plan",
]
