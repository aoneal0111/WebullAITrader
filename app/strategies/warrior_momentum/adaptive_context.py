"""Transparent, bounded adaptive context for Warrior entry assessment.

This module is deliberately deterministic and in-memory.  It supplies context
for the PAPER Warrior path only; it never authorizes an order or bypasses hard
safety checks.
"""
from __future__ import annotations

from collections import defaultdict, deque
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
    DAILY_PARTICIPATION_WEAK = "DAILY_PARTICIPATION_WEAK"
    DAILY_PARTICIPATION_STRONG = "DAILY_PARTICIPATION_STRONG"
    PREMARKET_BOOTSTRAP_LIQUIDITY_LOW = "PREMARKET_BOOTSTRAP_LIQUIDITY_LOW"
    PREMARKET_BOOTSTRAP_LIQUIDITY_MET = "PREMARKET_BOOTSTRAP_LIQUIDITY_MET"
    EXECUTABLE_LIQUIDITY_WEAK = "EXECUTABLE_LIQUIDITY_WEAK"
    EXECUTABLE_LIQUIDITY_ACCEPTABLE = "EXECUTABLE_LIQUIDITY_ACCEPTABLE"


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
    daily_dollar_volume: Decimal = Decimal("0")
    participation_velocity: Decimal = Decimal("0")
    bootstrap_required: bool = False


@dataclass(slots=True)
class _SymbolState:
    trading_date: object | None = None
    session: str = "UNKNOWN"
    observations: int = 0
    first_seen: object | None = None
    prior_dollar_volume: Decimal | None = None
    prior_volume: Decimal | None = None
    current_volume: Decimal = Decimal("0")
    volume_delta: Decimal = Decimal("0")
    current_dollar_volume: Decimal = Decimal("0")
    dollar_volume_delta: Decimal = Decimal("0")
    prior_timestamp: object | None = None
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
        self._active_date: object | None = None
        self._session_metrics: dict[str, dict[str, deque[Decimal]]] = defaultdict(
            lambda: {
                "spread": deque(maxlen=self._window),
                "dollar": deque(maxlen=self._window),
                "move": deque(maxlen=self._window),
            }
        )

    @property
    def symbol_state_count(self) -> int:
        return len(self._symbols)

    def evaluate(self, candidate: MomentumCandidate) -> AdaptiveContextResult:
        symbol = candidate.symbol.strip().upper()
        trading_date = candidate.timestamp.date()
        session = str(candidate.session or "UNKNOWN").upper()
        if self._active_date != trading_date:
            # Session distributions are scoped to the active trading date;
            # retaining yesterday's percentiles would silently contaminate
            # today's participation context.
            self._session_metrics.clear()
            self._active_date = trading_date
        state = self._symbols.get(symbol)
        if state is None:
            if len(self._symbols) >= self._max_symbols:
                self._symbols.pop(next(iter(self._symbols)))
            state = _SymbolState()
            self._symbols[symbol] = state
        if state.trading_date != trading_date:
            # Intraday state must never leak across trading dates.  Session
            # distributions intentionally remain bounded context for the
            # active process, while per-symbol trajectory resets here.
            state = _SymbolState(trading_date=trading_date, session=session,
                                 first_seen=candidate.timestamp)
            self._symbols[symbol] = state
        elif state.first_seen is None:
            state.first_seen = candidate.timestamp
        state.observations += 1

        spread = candidate.spread_percent
        dollar = candidate.dollar_volume
        move = candidate.percentage_change
        metrics = self._session_metrics[session]
        prior_dollar = state.prior_dollar_volume
        delta_dollar = max(Decimal("0"), dollar - prior_dollar) if prior_dollar is not None else Decimal("0")
        elapsed_minutes = Decimal("0")
        if state.prior_timestamp is not None:
            elapsed = (candidate.timestamp - state.prior_timestamp).total_seconds()
            elapsed_minutes = max(Decimal("0.0167"), Decimal(str(elapsed)) / Decimal("60"))
        velocity = delta_dollar / elapsed_minutes if elapsed_minutes > 0 else Decimal("0")
        daily_score = _linear(dollar, Decimal("100000"), Decimal("10000000"))
        velocity_score = _linear(velocity, Decimal("100000"), Decimal("1000000"))
        dollar_context = _percentile(dollar, metrics["dollar"])
        participation = _clip(Decimal("0.40") * daily_score + Decimal("0.30") * velocity_score
                              + Decimal("0.20") * dollar_context
                              + Decimal("0.10") * _linear(move, Decimal("5"), Decimal("40")))
        momentum = _clip(Decimal("0.7") * _linear(move, Decimal("0"), Decimal("40"))
                         + Decimal("0.3") * _linear(candidate.score.total, Decimal("25"), Decimal("85")))
        liquidity = _linear(dollar, Decimal("100000"), Decimal("10000000"))
        execution = Decimal("0") if spread is None else _clip(Decimal("1") - spread / self.catastrophic_spread_percent)
        if spread is not None and metrics["spread"]:
            # A spread that is high in absolute terms but ordinary for this
            # session remains contextual; the catastrophic ceiling stays hard.
            execution = _clip(Decimal("0.75") * execution + Decimal("0.25") * (Decimal("1") - _percentile(spread, metrics["spread"])))
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
        if participation < Decimal("0.45"):
            reasons.append(AdaptiveReason.DAILY_PARTICIPATION_WEAK)
        elif participation >= Decimal("0.65"):
            reasons.append(AdaptiveReason.DAILY_PARTICIPATION_STRONG)
        if candidate.relative_volume < Decimal("2.5") and participation >= Decimal("0.55"):
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
        bootstrap_required = session == "PREMARKET" and state.observations <= 3
        bootstrap_low = bootstrap_required and dollar < self.minimum_dollar_volume
        # A low accumulated amount is not permanently disqualifying once the
        # symbol demonstrates real intraday velocity and repeated evidence.
        improving_liquidity = velocity_score >= Decimal("0.55") and momentum >= Decimal("0.75") and state.observations >= 2
        hard_block = (freshness == 0 or spread is None or spread >= self.catastrophic_spread_percent
                      or (bootstrap_low and not improving_liquidity)
                      or (dollar < self.minimum_dollar_volume and not improving_liquidity))
        if bootstrap_low:
            reasons.append(AdaptiveReason.PREMARKET_BOOTSTRAP_LIQUIDITY_LOW)
        elif session == "PREMARKET" and dollar >= self.minimum_dollar_volume:
            reasons.append(AdaptiveReason.PREMARKET_BOOTSTRAP_LIQUIDITY_MET)
        if dollar < self.minimum_dollar_volume and not improving_liquidity:
            reasons.append(AdaptiveReason.EXECUTABLE_LIQUIDITY_WEAK)
        elif dollar >= self.minimum_dollar_volume or improving_liquidity:
            reasons.append(AdaptiveReason.EXECUTABLE_LIQUIDITY_ACCEPTABLE)
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
        prior_volume = state.prior_volume
        state.prior_dollar_volume = dollar
        state.prior_volume = candidate.volume
        state.current_volume = candidate.volume
        state.volume_delta = max(Decimal("0"), candidate.volume - prior_volume) if prior_volume is not None else Decimal("0")
        state.current_dollar_volume = dollar
        state.dollar_volume_delta = delta_dollar
        state.prior_timestamp = candidate.timestamp
        state.session = session
        metrics["spread"].append(spread or Decimal("0")); metrics["dollar"].append(dollar); metrics["move"].append(move)
        return AdaptiveContextResult(momentum, participation, liquidity, execution, structure, freshness, score,
                                     decision, tuple(reasons), state.observations, dollar, velocity,
                                     bootstrap_required)

    def permits_contextual_rvol_spread(self, candidate: MomentumCandidate) -> bool:
        result = self.evaluate(candidate)
        return result.decision in {AdaptiveDecision.FORMING, AdaptiveDecision.READY}

__all__ = ["AdaptiveDecision", "AdaptiveReason", "AdaptiveContextResult", "WarriorAdaptiveContext"]
