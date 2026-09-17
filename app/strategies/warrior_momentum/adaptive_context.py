"""Transparent, bounded adaptive context for Warrior entry assessment.

This module is deliberately deterministic and in-memory.  It supplies context
for the PAPER Warrior path only; it never authorizes an order or bypasses hard
safety checks.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .models import MomentumCandidate, ReasonCode, SetupState


class AdaptiveDecision(StrEnum):
    REJECT = "ADAPTIVE_REJECT"
    WATCH = "ADAPTIVE_WATCH"
    FORMING = "ADAPTIVE_FORMING"
    READY = "ADAPTIVE_READY"


class AdaptiveReason(StrEnum):
    MOMENTUM_EXCEPTIONAL = "MOMENTUM_EXCEPTIONAL"
    PARTICIPATION_IMPROVING = "PARTICIPATION_IMPROVING"
    RVOL_BELOW_TYPICAL_BUT_SUPPORTED = "RVOL_BELOW_TYPICAL_BUT_SUPPORTED"
    LIQUIDITY_STRONG = "LIQUIDITY_STRONG"
    SPREAD_ELEVATED_BUT_ACCEPTABLE = "SPREAD_ELEVATED_BUT_ACCEPTABLE"
    SPREAD_COST_EXCESSIVE = "SPREAD_COST_EXCESSIVE"
    STRUCTURE_IMPROVING = "STRUCTURE_IMPROVING"
    FRESHNESS_BLOCKED = "FRESHNESS_BLOCKED"
    ABSOLUTE_LIQUIDITY_BLOCKED = "ABSOLUTE_LIQUIDITY_BLOCKED"


@dataclass(frozen=True, slots=True)
class AdaptiveContextResult:
    momentum_strength: Decimal
    participation: Decimal
    liquidity: Decimal
    execution_cost: Decimal
    structure_quality: Decimal
    freshness: Decimal
    opportunity_score: Decimal
    decision: AdaptiveDecision
    reasons: tuple[AdaptiveReason, ...]
    observation_count: int


@dataclass(slots=True)
class _SymbolState:
    observations: int = 0
    prior_participation: Decimal | None = None
    prior_score: Decimal | None = None


def _clip(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("1"), value))


def _linear(value: Decimal | None, weak: Decimal, strong: Decimal) -> Decimal:
    if value is None or value <= weak:
        return Decimal("0")
    if value >= strong:
        return Decimal("1")
    return _clip((value - weak) / (strong - weak))


def _percentile(value: Decimal, values: deque[Decimal]) -> Decimal:
    """Return a bounded empirical percentile without retaining raw history."""
    if not values:
        return Decimal("0.5")
    below = sum(1 for item in values if item <= value)
    return Decimal(below) / Decimal(len(values))


class WarriorAdaptiveContext:
    """Bounded rolling session context; O(1) update and no external I/O."""

    def __init__(self, *, max_symbols: int = 512, window: int = 128,
                 catastrophic_spread_percent: Decimal = Decimal("5"),
                 minimum_dollar_volume: Decimal = Decimal("250000"),
                 ready_score: Decimal = Decimal("0.68")) -> None:
        self._max_symbols = max(1, int(max_symbols))
        self._window = max(8, int(window))
        self.catastrophic_spread_percent = catastrophic_spread_percent
        self.minimum_dollar_volume = minimum_dollar_volume
        self.ready_score = ready_score
        self._symbols: dict[str, _SymbolState] = {}
        self._rvol: deque[Decimal] = deque(maxlen=self._window)
        self._spread: deque[Decimal] = deque(maxlen=self._window)
        self._dollar: deque[Decimal] = deque(maxlen=self._window)
        self._move: deque[Decimal] = deque(maxlen=self._window)

    @property
    def symbol_state_count(self) -> int:
        return len(self._symbols)

    def evaluate(self, candidate: MomentumCandidate) -> AdaptiveContextResult:
        symbol = candidate.symbol.strip().upper()
        state = self._symbols.get(symbol)
        if state is None:
            if len(self._symbols) >= self._max_symbols:
                self._symbols.pop(next(iter(self._symbols)))
            state = _SymbolState()
            self._symbols[symbol] = state
        state.observations += 1

        rvol = candidate.relative_volume
        spread = candidate.spread_percent
        dollar = candidate.dollar_volume
        move = candidate.percentage_change
        rvol_score = _linear(rvol, Decimal("1"), Decimal("10"))
        dollar_score = _linear(dollar, Decimal("100000"), Decimal("10000000"))
        rvol_context = _percentile(rvol, self._rvol)
        dollar_context = _percentile(dollar, self._dollar)
        participation = _clip(Decimal("0.30") * rvol_score + Decimal("0.35") * dollar_score
                              + Decimal("0.20") * rvol_context + Decimal("0.15") * dollar_context)
        momentum = _clip(Decimal("0.7") * _linear(move, Decimal("0"), Decimal("40"))
                         + Decimal("0.3") * _linear(candidate.score.total, Decimal("25"), Decimal("85")))
        liquidity = _linear(dollar, Decimal("100000"), Decimal("10000000"))
        execution = Decimal("0") if spread is None else _clip(Decimal("1") - spread / self.catastrophic_spread_percent)
        if spread is not None and self._spread:
            # A spread that is high in absolute terms but ordinary for this
            # session remains contextual; the catastrophic ceiling stays hard.
            execution = _clip(Decimal("0.75") * execution + Decimal("0.25") * (Decimal("1") - _percentile(spread, self._spread)))
        structure = Decimal("0") if candidate.setup is None else _clip(candidate.setup.score / Decimal("100"))
        freshness = Decimal("0") if ReasonCode.STALE_MARKET_DATA in candidate.reason_codes else Decimal("1")
        score = _clip(Decimal("0.25") * momentum + Decimal("0.25") * participation
                      + Decimal("0.15") * liquidity + Decimal("0.15") * execution
                      + Decimal("0.15") * structure + Decimal("0.05") * freshness)

        reasons: list[AdaptiveReason] = []
        if momentum >= Decimal("0.75"):
            reasons.append(AdaptiveReason.MOMENTUM_EXCEPTIONAL)
        if state.prior_participation is not None and participation > state.prior_participation:
            reasons.append(AdaptiveReason.PARTICIPATION_IMPROVING)
        if rvol < Decimal("2.5") and participation >= Decimal("0.55"):
            reasons.append(AdaptiveReason.RVOL_BELOW_TYPICAL_BUT_SUPPORTED)
        if liquidity >= Decimal("0.65"):
            reasons.append(AdaptiveReason.LIQUIDITY_STRONG)
        if spread is not None and spread > Decimal("1.25") and execution >= Decimal("0.35"):
            reasons.append(AdaptiveReason.SPREAD_ELEVATED_BUT_ACCEPTABLE)
        if spread is None or spread >= self.catastrophic_spread_percent:
            reasons.append(AdaptiveReason.SPREAD_COST_EXCESSIVE)
        if structure >= Decimal("0.6"):
            reasons.append(AdaptiveReason.STRUCTURE_IMPROVING)
        if freshness == 0:
            reasons.append(AdaptiveReason.FRESHNESS_BLOCKED)
        if dollar < self.minimum_dollar_volume:
            reasons.append(AdaptiveReason.ABSOLUTE_LIQUIDITY_BLOCKED)

        hard_block = freshness == 0 or spread is None or spread >= self.catastrophic_spread_percent or dollar < self.minimum_dollar_volume
        if hard_block:
            decision = AdaptiveDecision.REJECT
        elif score >= self.ready_score and candidate.setup is not None and candidate.setup.state is SetupState.TRIGGERED:
            decision = AdaptiveDecision.READY
        elif score >= Decimal("0.45"):
            decision = AdaptiveDecision.FORMING if candidate.setup is not None else AdaptiveDecision.WATCH
        else:
            decision = AdaptiveDecision.REJECT
        state.prior_participation = participation
        state.prior_score = score
        self._rvol.append(rvol); self._spread.append(spread or Decimal("0")); self._dollar.append(dollar); self._move.append(move)
        return AdaptiveContextResult(momentum, participation, liquidity, execution, structure, freshness, score,
                                     decision, tuple(reasons), state.observations)

    def permits_contextual_rvol_spread(self, candidate: MomentumCandidate) -> bool:
        result = self.evaluate(candidate)
        return result.decision in {AdaptiveDecision.FORMING, AdaptiveDecision.READY}


__all__ = ["AdaptiveDecision", "AdaptiveReason", "AdaptiveContextResult", "WarriorAdaptiveContext"]
