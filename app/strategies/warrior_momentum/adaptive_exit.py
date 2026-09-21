"""Adaptive, evidence-aware risk geometry and soft-exit thresholds.

The hard protective stop remains broker-owned and may never be widened after
entry.  Optional order-book and footprint evidence changes only soft
profit-defense timing; missing or stale evidence is neutral.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from .models import MinuteBar, MomentumEntrySignal

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class AdaptiveExitEvidence:
    evaluated_at: datetime
    quote_fresh: bool
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    depth_imbalance: Decimal | None = None
    microprice: Decimal | None = None
    flow_classification: str | None = None
    flow_fresh: bool = False


@dataclass(frozen=True, slots=True)
class AdaptiveExitAssessment:
    tighten_giveback_r: Decimal
    runner_exit_giveback_r: Decimal
    pressure_score: int
    volatility_r: Decimal
    reasons: tuple[str, ...]


def true_ranges(bars: Sequence[MinuteBar], *, lookback: int) -> tuple[Decimal, ...]:
    """Return bounded completed-bar true ranges."""
    if lookback <= 0 or not bars:
        return ()
    selected = tuple(bars[-(lookback + 1):])
    values: list[Decimal] = []
    previous_close: Decimal | None = None
    for bar in selected:
        intrabar = max(ZERO, bar.high - bar.low)
        value = intrabar
        if previous_close is not None:
            value = max(
                value,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        values.append(value)
        previous_close = bar.close
    return tuple(values[-lookback:])


def adapt_initial_stop(
    signal: MomentumEntrySignal,
    bars: Sequence[MinuteBar],
    *,
    spread_percent: Decimal | None,
    config: object,
    maximum_risk_per_share: Decimal,
) -> MomentumEntrySignal:
    """Add pre-entry volatility/spread room while preserving dollar-risk sizing.

    This runs before position sizing.  A wider stop therefore reduces shares;
    it never adds post-entry risk.  The detector's structural stop is retained
    separately so a completed-bar thesis failure can still exit ahead of the
    catastrophe stop.
    """
    if not bool(getattr(config, "adaptive_exit_enabled", False)):
        return signal
    structural_stop = signal.structural_stop_price or signal.stop_price
    original_risk = signal.entry_trigger - signal.stop_price
    if original_risk <= ZERO:
        return signal
    ranges = true_ranges(
        bars, lookback=int(getattr(config, "exit_range_lookback", 5))
    )
    average_range = (
        ZERO if not ranges else sum(ranges, ZERO) / Decimal(len(ranges))
    )
    volatility_risk = average_range * Decimal(
        getattr(config, "initial_stop_volatility_multiplier", Decimal("1"))
    )
    spread_risk = ZERO
    if spread_percent is not None and spread_percent >= ZERO:
        spread_risk = (
            signal.entry_trigger * spread_percent / HUNDRED
            * Decimal(getattr(config, "initial_stop_spread_multiplier", Decimal("1")))
        )
    required_risk = max(original_risk, volatility_risk, spread_risk)
    widening_cap = original_risk * Decimal(
        getattr(config, "initial_stop_max_widening_r", Decimal("1"))
    )
    bounded_risk = min(required_risk, widening_cap, maximum_risk_per_share)
    if bounded_risk <= original_risk:
        if signal.structural_stop_price is None:
            return replace(signal, structural_stop_price=structural_stop)
        return signal
    hard_stop = signal.entry_trigger - bounded_risk
    if hard_stop <= ZERO:
        return signal
    return replace(
        signal,
        stop_price=hard_stop,
        risk_per_share=bounded_risk,
        target_levels=(
            signal.entry_trigger + bounded_risk,
            signal.entry_trigger + bounded_risk * 2,
            signal.entry_trigger + bounded_risk * 3,
        ),
        structural_stop_price=structural_stop,
    )


def assess_adaptive_exit(
    *,
    base_tighten_giveback_r: Decimal,
    base_runner_exit_giveback_r: Decimal,
    recent_ranges: Sequence[Decimal],
    risk_per_share: Decimal,
    evidence: AdaptiveExitEvidence | None,
    config: object,
) -> AdaptiveExitAssessment:
    """Blend volatility, NBBO depth and footprint into soft-exit room."""
    volatility_r = ZERO
    if risk_per_share > ZERO and recent_ranges:
        volatility_r = (
            sum(recent_ranges, ZERO) / Decimal(len(recent_ranges))
        ) / risk_per_share
    baseline = Decimal(getattr(config, "exit_volatility_baseline_r", ZERO))
    maximum_allowance = Decimal(
        getattr(config, "exit_volatility_allowance_max_r", ZERO)
    )
    volatility_allowance = min(
        maximum_allowance, max(ZERO, volatility_r - baseline)
    )

    score = 0
    reasons: list[str] = []
    if evidence is not None and evidence.quote_fresh:
        flow = (
            evidence.flow_classification
            if evidence.flow_fresh else None
        )
        if flow == "STRONG_ACCUMULATION":
            score += 2
            reasons.append("FLOW_STRONG_ACCUMULATION")
        elif flow == "SUPPORTIVE":
            score += 1
            reasons.append("FLOW_SUPPORTIVE")
        elif flow == "STRONG_DISTRIBUTION":
            score -= 2
            reasons.append("FLOW_STRONG_DISTRIBUTION")
        elif flow == "DISTRIBUTION":
            score -= 1
            reasons.append("FLOW_DISTRIBUTION")

        threshold = Decimal(
            getattr(config, "exit_depth_imbalance_threshold", Decimal("0.15"))
        )
        if evidence.depth_imbalance is not None:
            if evidence.depth_imbalance >= threshold:
                score += 1
                reasons.append("DEPTH_BID_SUPPORT")
            elif evidence.depth_imbalance <= -threshold:
                score -= 1
                reasons.append("DEPTH_ASK_PRESSURE")

        if (
            evidence.microprice is not None
            and evidence.best_bid is not None
            and evidence.best_ask is not None
        ):
            midpoint = (evidence.best_bid + evidence.best_ask) / 2
            if evidence.microprice > midpoint:
                score += 1
                reasons.append("MICROPRICE_ABOVE_MID")
            elif evidence.microprice < midpoint:
                score -= 1
                reasons.append("MICROPRICE_BELOW_MID")

    pressure_adjustment = ZERO
    if score >= 2:
        pressure_adjustment = Decimal(
            getattr(config, "exit_supportive_allowance_r", ZERO)
        )
    elif score <= -2:
        pressure_adjustment = -Decimal(
            getattr(config, "exit_adverse_reduction_r", ZERO)
        )
    minimum = Decimal(getattr(config, "exit_minimum_giveback_r", ZERO))
    tighten = max(
        minimum,
        base_tighten_giveback_r + volatility_allowance + pressure_adjustment,
    )
    runner = max(
        tighten,
        base_runner_exit_giveback_r + volatility_allowance + pressure_adjustment,
    )
    if volatility_allowance > ZERO:
        reasons.append("VOLATILITY_ALLOWANCE")
    if evidence is None or not evidence.quote_fresh:
        reasons.append("MICROSTRUCTURE_NEUTRAL")
    return AdaptiveExitAssessment(
        tighten, runner, score, volatility_r, tuple(reasons)
    )
