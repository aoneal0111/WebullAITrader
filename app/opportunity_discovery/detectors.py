"""Pure deterministic detector objects over bounded completed-bar context."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Protocol

from app.strategies.warrior_momentum.models import MinuteBar
from app.strategies.warrior_momentum.post_gap_reclaim_research import (
    PostGapCandidateContext, PostGapReclaimState, detect_post_gap_reclaim,
)

from .context import build_impulse, build_pullback, build_reference_levels, structural_anchor
from .contracts import (
    DetectionState, DetectorAvailability, DiscoveryContext, StrategyDefinition,
    StrategyDetection,
)
from .taxonomy import STRATEGY_TAXONOMY

HUNDRED = Decimal("100")


class ResearchDetector(Protocol):
    definition: StrategyDefinition
    def detect(self, context: DiscoveryContext) -> StrategyDetection: ...


@dataclass(frozen=True, slots=True)
class _Analysis:
    context: DiscoveryContext
    impulse: object
    pullback: object
    levels: object
    anchor: str


@dataclass(frozen=True, slots=True)
class RuleDetector:
    definition: StrategyDefinition
    evaluator: Callable[[_Analysis], tuple[DetectionState, Decimal | None, Decimal | None, tuple[str, ...], tuple[tuple[str, Decimal], ...]]]

    def detect(self, context: DiscoveryContext) -> StrategyDetection:
        missing = _missing(context, self.definition.required_features)
        anchor = structural_anchor(context, build_impulse(context))
        if missing:
            return _result(self.definition, context, DetectionState.UNAVAILABLE, anchor, None, None,
                           ("MISSING_REQUIRED_FEATURE",), (), missing)
        impulse = build_impulse(context)
        analysis = _Analysis(context, impulse, build_pullback(context, impulse),
                             build_reference_levels(context, impulse), anchor)
        state, trigger, stop, reasons, quality = self.evaluator(analysis)
        return _result(self.definition, context, state, anchor, trigger, stop, reasons, quality, ())


@dataclass(frozen=True, slots=True)
class UnavailableDetector:
    definition: StrategyDefinition

    def detect(self, context: DiscoveryContext) -> StrategyDetection:
        anchor = structural_anchor(context, build_impulse(context))
        return _result(self.definition, context, DetectionState.UNAVAILABLE, anchor, None, None,
                       (self.definition.availability.value, self.definition.unavailable_reason or "UNAVAILABLE"), (),
                       self.definition.required_features)


class DetectorRegistry:
    def __init__(self, detectors: tuple[ResearchDetector, ...]) -> None:
        identities = [item.definition.strategy_id for item in detectors]
        if len(identities) != len(set(identities)):
            raise ValueError("detector strategy IDs must be unique")
        if any(not item.definition.research_only for item in detectors):
            raise ValueError("all registered detectors must be research-only")
        self._detectors = detectors

    @property
    def detectors(self):
        return self._detectors

    def evaluate(self, context: DiscoveryContext):
        return tuple(detector.detect(context) for detector in self._detectors)


def default_registry() -> DetectorRegistry:
    evaluators = {
        "MICRO_PULLBACK": _micro_pullback,
        "FIRST_PULLBACK": _first_pullback,
        "HIGHER_LOW_CONTINUATION": _higher_low,
        "SHALLOW_PULLBACK_CONTINUATION": _shallow,
        "DEEP_PULLBACK_RECLAIM": _deep_reclaim,
        "VOLUME_CONTRACTION_PULLBACK": _volume_pullback,
        "MOMENTUM_REACCELERATION": _reacceleration,
        "HIGH_OF_DAY_BREAKOUT": _hod_breakout,
        "FLAT_TOP_BREAKOUT": _flat_top,
        "CONSOLIDATION_BREAKOUT": _consolidation,
        "ASCENDING_BASE_BREAKOUT": _ascending,
        "RANGE_COMPRESSION_BREAKOUT": _compression,
        "BREAKOUT_RETEST_CONTINUATION": _retest,
        "OPENING_RANGE_BREAKOUT": _opening_range,
        "PREMARKET_HIGH_BREAKOUT": _premarket_high,
        "PREMARKET_CONSOLIDATION_BREAKOUT": _premarket_consolidation,
        "OPENING_DRIVE_CONTINUATION": _opening_drive,
        "FAILED_BREAKOUT_RECLAIM": _failed_reclaim,
        "HOD_RECLAIM": _hod_reclaim,
        "GAP_AND_GO_CONTINUATION": _gap_go,
        "POST_GAP_RECLAIM": _post_gap_adapter,
        "DIP_AND_RIP": _dip_rip,
        "MOMENTUM_SQUEEZE_EXPANSION": _squeeze,
    }
    detectors = []
    for definition in STRATEGY_TAXONOMY:
        evaluator = evaluators.get(definition.strategy_id)
        detectors.append(RuleDetector(definition, evaluator) if evaluator is not None else UnavailableDetector(definition))
    return DetectorRegistry(tuple(detectors))


def _pullback(a, predicate, reason):
    p, impulse = a.pullback, a.impulse
    if p is None or impulse is None:
        return DetectionState.NOT_DETECTED, None, None, ("NO_COMPLETED_PULLBACK",), ()
    if p.invalidated:
        return DetectionState.INVALIDATED, impulse.end_price, p.lowest_price, ("STRUCTURAL_LOW_FAILED",), ()
    detected = predicate(p, impulse) and a.context.completed_bars[-1].close >= a.context.completed_bars[-1].open
    quality = (("impulse_percent", impulse.percentage_move), ("pullback_depth_percent", p.depth_percent))
    return (DetectionState.DETECTED if detected else DetectionState.FORMING,
            impulse.end_price, p.lowest_price, (reason if detected else "PULLBACK_FORMING",), quality)


def _micro_pullback(a): return _pullback(a, lambda p, i: p.bars <= 2 and p.depth_relative_to_impulse <= Decimal("0.35"), "MICRO_PULLBACK_RESUMPTION")
def _first_pullback(a): return _pullback(a, lambda p, i: 1 <= p.bars <= 4 and i.percentage_move >= 2, "FIRST_ORDERLY_PULLBACK")
def _higher_low(a): return _pullback(a, lambda p, i: p.higher_low, "HIGHER_LOW_CONFIRMED")
def _shallow(a): return _pullback(a, lambda p, i: p.depth_relative_to_impulse <= Decimal("0.35"), "SHALLOW_RETRACEMENT")
def _deep_reclaim(a): return _pullback(a, lambda p, i: p.depth_relative_to_impulse >= Decimal("0.45") and a.context.completed_bars[-1].close >= i.start_price + i.absolute_move / 2, "DEEP_PULLBACK_MIDPOINT_RECLAIM")
def _volume_pullback(a): return _pullback(a, lambda p, i: p.volume_contraction is not None and p.volume_contraction <= Decimal("0.70"), "PULLBACK_VOLUME_CONTRACTED")
def _reacceleration(a): return _pullback(a, lambda p, i: _last_range(a) > _previous_range(a), "MOMENTUM_RANGE_REACCELERATED")
def _dip_rip(a): return _pullback(a, lambda p, i: Decimal("0.45") <= p.depth_relative_to_impulse < 1 and _last_range(a) > _previous_range(a), "DEEP_VALID_DIP_REACCELERATED")


def _hod_breakout(a): return _break(a, max((b.high for b in a.context.completed_bars[:-1]), default=None), "HOD_BROKEN")
def _flat_top(a):
    prior = a.context.completed_bars[-4:-1]
    level = max((b.high for b in prior), default=None)
    tight = len(prior) == 3 and level and (level - min(b.high for b in prior)) / level <= Decimal("0.005")
    return _break(a, level if tight else None, "FLAT_TOP_BROKEN")
def _consolidation(a):
    prior = a.context.completed_bars[-5:-1]
    level = max((b.high for b in prior), default=None)
    tight = len(prior) == 4 and level and (level - min(b.low for b in prior)) / level <= Decimal("0.03")
    return _break(a, level if tight else None, "CONSOLIDATION_BROKEN")
def _ascending(a):
    prior = a.context.completed_bars[-4:-1]
    valid = len(prior) == 3 and all(prior[i].low < prior[i + 1].low for i in range(2))
    return _break(a, max((b.high for b in prior), default=None) if valid else None, "ASCENDING_BASE_BROKEN")
def _compression(a):
    prior = a.context.completed_bars[-4:-1]
    ranges = [b.high - b.low for b in prior]
    valid = len(ranges) == 3 and ranges[0] > ranges[1] > ranges[2] and _last_range(a) > sum(ranges) / 3
    return _break(a, max((b.high for b in prior), default=None) if valid else None, "RANGE_COMPRESSION_EXPANDED")
def _squeeze(a):
    state, trigger, stop, _, quality = _compression(a)
    if state is DetectionState.DETECTED and a.context.completed_bars[-1].volume > a.context.completed_bars[-2].volume * Decimal("1.5"):
        return state, trigger, stop, ("RANGE_AND_VOLUME_EXPANSION",), quality
    return DetectionState.FORMING if trigger else DetectionState.NOT_DETECTED, trigger, stop, ("SQUEEZE_NOT_EXPANDED",), quality


def _retest(a):
    bars = a.context.completed_bars
    if len(bars) < 5:
        return _none("INSUFFICIENT_RETEST_BARS")
    level = max(b.high for b in bars[:-3])
    broke = bars[-3].close > level
    retested = bars[-2].low <= level * Decimal("1.005") and bars[-2].close >= level * Decimal("0.99")
    detected = broke and retested and bars[-1].close > max(bars[-2].high, level)
    return _structured(detected, level, bars[-2].low, "BREAKOUT_RETEST_CONTINUED")


def _opening_range(a): return _break(a, a.levels.opening_range_high, "OPENING_RANGE_BROKEN")
def _premarket_high(a):
    if not any(b.session.upper() == "REGULAR" for b in a.context.completed_bars[-1:]): return _none("NOT_REGULAR_SESSION_BREAK")
    return _break(a, a.levels.premarket_high, "PREMARKET_HIGH_BROKEN")
def _premarket_consolidation(a):
    if a.context.completed_bars[-1].session.upper() != "PREMARKET": return _none("NOT_PREMARKET")
    return _consolidation(a)
def _opening_drive(a):
    regular = [b for b in a.context.completed_bars if b.session.upper() == "REGULAR"]
    if not 3 <= len(regular) <= 15: return _none("OUTSIDE_OPENING_DRIVE_WINDOW")
    detected = regular[-1].close > regular[0].open and sum(b.close > b.open for b in regular) >= len(regular) - 1
    return _structured(detected, regular[-2].high, min(b.low for b in regular), "OPENING_DRIVE_CONTINUED")


def _failed_reclaim(a):
    bars = a.context.completed_bars
    if len(bars) < 4: return _none("INSUFFICIENT_RECLAIM_BARS")
    level = max(b.high for b in bars[:-3])
    detected = bars[-3].high > level and bars[-2].close < level and bars[-1].close > level
    return _structured(detected, level, min(bars[-2].low, bars[-1].low), "FAILED_BREAKOUT_RECLAIMED")
def _hod_reclaim(a):
    bars = a.context.completed_bars
    if len(bars) < 3: return _none("INSUFFICIENT_HOD_RECLAIM_BARS")
    level = max(b.high for b in bars[:-2])
    detected = bars[-2].close < level and bars[-1].close > level
    return _structured(detected, level, bars[-2].low, "HOD_REGION_RECLAIMED")
def _gap_go(a):
    regular = [b for b in a.context.completed_bars if b.session.upper() == "REGULAR"]
    prior = a.context.prior_close
    if prior is None or not regular: return _none("PRIOR_CLOSE_UNAVAILABLE")
    gap = (regular[0].open - prior) / prior * HUNDRED
    detected = gap >= 4 and regular[-1].close > regular[0].open and regular[-1].low > prior
    return _structured(detected, regular[0].high, min(b.low for b in regular), "GAP_HELD_AND_CONTINUED", (("gap_percent", gap),))


def _post_gap_adapter(a):
    c = a.context
    required = (c.percentage_change, c.relative_volume, c.dollar_volume)
    if any(value is None for value in required): return _none("POST_GAP_CANDIDATE_CONTEXT_UNAVAILABLE")
    bars = tuple(MinuteBar(b.symbol, b.completed_at, b.open, b.high, b.low, b.close, b.volume) for b in c.completed_bars)
    hod = max(bar.high for bar in c.completed_bars)
    distance_hod = (hod - c.completed_bars[-1].close) / hod * HUNDRED
    result = detect_post_gap_reclaim(bars, PostGapCandidateContext(True, c.percentage_change,
        c.relative_volume, c.dollar_volume, c.spread_percent, c.float_shares, distance_hod))
    detected = result.state is PostGapReclaimState.SETUP_TRIGGERED
    plan = result.plan
    return (DetectionState.DETECTED if detected else DetectionState.FORMING,
            None if plan is None else plan.trigger, None if plan is None else plan.stop,
            ("EXISTING_POST_GAP_RECLAIM_ADAPTER", result.state.value),
            () if result.flush_percent is None else (("flush_percent", result.flush_percent),))


def _break(a, level, reason):
    bars = a.context.completed_bars
    if level is None or len(bars) < 2: return _none("REFERENCE_LEVEL_UNAVAILABLE")
    detected = bars[-1].close > level and bars[-2].close <= level
    stop = min(b.low for b in bars[-3:])
    return _structured(detected, level, stop, reason)


def _structured(detected, trigger, stop, reason, quality=()):
    return (DetectionState.DETECTED if detected else DetectionState.NOT_DETECTED,
            trigger, stop, (reason if detected else "STRUCTURE_NOT_CONFIRMED",), quality)
def _none(reason): return DetectionState.NOT_DETECTED, None, None, (reason,), ()
def _last_range(a):
    b=a.context.completed_bars[-1]; return b.high-b.low
def _previous_range(a):
    b=a.context.completed_bars[-2]; return b.high-b.low


def _missing(context, names):
    caps = context.capabilities
    result = []
    for name in names:
        available = getattr(caps, name, False)
        if name == "prior_close": available = available and context.prior_close is not None
        if name == "authoritative_vwap": available = available and context.vwap is not None
        if not available: result.append(name)
    return tuple(result)


def _result(definition, context, state, anchor, trigger, stop, reasons, quality, missing):
    # Detector geometry may identify a structure whose candidate stop is no
    # longer below its reference/trigger (for example after a gap or a retest
    # that never offered positive technical risk).  That remains useful
    # observational evidence, but it is not a complete R plan.  Preserve the
    # detection and make the unavailable plan explicit instead of allowing one
    # detector to fail the entire multi-strategy evaluation cycle.
    if trigger is not None and stop is not None and trigger <= stop:
        stop = None
        reasons = tuple(reasons) + ("INSUFFICIENT_R_PLAN_NONPOSITIVE_RISK",)
    observed = tuple(name for name in definition.required_features if name not in missing)
    optional = tuple(name for name in definition.optional_features if not _missing(context, (name,)))
    reference = None if not context.completed_bars else context.completed_bars[-1].close
    setup_anchor = f"{anchor}|{definition.strategy_id}"
    return StrategyDetection(definition.strategy_id, definition.strategy_version, definition.family,
        context.symbol.upper(), context.session.upper(), context.session_date, context.decision_cutoff,
        state, setup_anchor, anchor, reference, trigger, stop, tuple(quality), observed, optional,
        tuple(missing), tuple(reasons), True)
