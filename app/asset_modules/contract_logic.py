"""Contract-aware PAPER planning. No broker authority or model-generated prices.

Underlying direction chooses calls/puts; all option risk and exits use premiums.
Futures risk uses contract multiplier, integer lots and supplied margin.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR

from app.asset_modules.engine_catalog import EngineId

D = Decimal


def _positive(*values: Decimal) -> None:
    if any(not isinstance(v, D) or not v.is_finite() or v <= 0 for v in values):
        raise ValueError("Finite positive Decimal values required")


def _aware(at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Aware timestamp required")


@dataclass(frozen=True)
class Contract:
    symbol: str
    engine: EngineId
    expires_at: datetime
    multiplier: Decimal
    tick: Decimal
    margin: Decimal
    underlying: str = ""
    right: str = ""
    strike: Decimal | None = None

    def __post_init__(self):
        _aware(self.expires_at)
        _positive(self.multiplier, self.tick, self.margin)
        if not self.symbol or self.engine not in {EngineId.OPTIONS, EngineId.FUTURES}:
            raise ValueError("Explicit supported contract identity required")
        if self.engine == EngineId.OPTIONS:
            if self.underlying not in {"SPY", "QQQ"} or self.right not in {"CALL", "PUT"}:
                raise ValueError("Options restricted to SPY/QQQ calls and puts")
            _positive(self.strike)


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    source_at: datetime
    observed_at: datetime

    def valid(self, now: datetime, max_age: timedelta = timedelta(seconds=2)) -> bool:
        _aware(now)
        _aware(self.source_at)
        _aware(self.observed_at)
        return (isinstance(self.bid, D) and isinstance(self.ask, D)
                and self.bid.is_finite() and self.ask.is_finite()
                and 0 < self.bid <= self.ask
                and timedelta(0) <= now - self.source_at <= max_age
                and self.source_at <= self.observed_at <= now)


@dataclass(frozen=True)
class Bar:
    end: datetime
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self):
        _aware(self.end)
        _positive(self.high, self.low, self.close)
        if not self.low <= self.close <= self.high:
            raise ValueError("Invalid OHLC geometry")


def direction(bars: tuple[Bar, ...], now: datetime) -> str | None:
    """20-bar range breakout with 8/21 EMA agreement; completed bars only."""
    _aware(now)
    if len(bars) < 22:
        return None
    if any(b.end > now for b in bars) or any(a.end >= b.end for a, b in zip(bars, bars[1:])):
        raise ValueError("Bars must be completed and strictly ordered")
    spacing = bars[-1].end - bars[-2].end
    if spacing <= timedelta(0) or spacing > timedelta(minutes=5):
        return None
    if now - bars[-1].end > spacing:
        return None
    if any(b.end - a.end != spacing for a, b in zip(bars[-22:], bars[-21:])):
        return None
    def ema(period):
        value = bars[0].close
        alpha = D(2) / D(period + 1)
        for bar in bars[1:]:
            value += alpha * (bar.close - value)
        return value
    latest, prior = bars[-1], bars[-21:-1]
    fast, slow = ema(8), ema(21)
    if latest.close > max(b.high for b in prior) and fast > slow:
        return "LONG"
    if latest.close < min(b.low for b in prior) and fast < slow:
        return "SHORT"
    return None


@dataclass(frozen=True)
class Plan:
    contract: Contract
    side: str
    quantity: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    fee_per_side: Decimal
    created_at: datetime
    deadline: datetime
    policy: str


def plan(contract: Contract, quote: Quote, now: datetime, *, side: str,
         stop_distance: Decimal, target_distance: Decimal, risk_budget: Decimal,
         capital_budget: Decimal, fee_per_side: Decimal, hold: timedelta) -> Plan | None:
    _positive(stop_distance, target_distance, risk_budget, capital_budget)
    _aware(now)
    if not isinstance(fee_per_side, D) or not fee_per_side.is_finite() or fee_per_side < 0:
        raise ValueError("Explicit nonnegative fees required")
    if side not in {"LONG", "SHORT"} or hold <= timedelta(0):
        raise ValueError("Invalid direction or hold")
    if contract.engine == EngineId.OPTIONS and side != "LONG":
        raise ValueError("Naked option selling is unsupported")
    if quote.symbol != contract.symbol or not quote.valid(now) or now + hold >= contract.expires_at:
        return None
    if stop_distance % contract.tick or target_distance % contract.tick:
        raise ValueError("Distances must respect contract tick")
    entry = quote.ask if side == "LONG" else quote.bid
    sign = D(1) if side == "LONG" else D(-1)
    stop, target = entry - sign * stop_distance, entry + sign * target_distance
    if min(stop, target) <= 0:
        return None
    spread = quote.ask - quote.bid
    costs = spread * contract.multiplier + 2 * fee_per_side
    price_risk = stop_distance * contract.multiplier
    if target_distance * contract.multiplier - costs < price_risk:
        return None
    # Options pay premium in full; futures consume declared contract margin.
    capital = (entry * contract.multiplier if contract.engine == EngineId.OPTIONS
               else contract.margin) + fee_per_side
    lots = int(min(risk_budget / (price_risk + costs), capital_budget / capital)
               .to_integral_value(rounding=ROUND_FLOOR))
    if lots < 1:
        return None
    return Plan(contract, side, lots, entry, stop, target, fee_per_side, now,
                now + hold, "CONTRACT_BREAKOUT_PAPER_V1")


def option_plans(contracts: tuple[Contract, ...], quotes: dict[str, Quote],
                 underlying: str, underlying_direction: str, now: datetime,
                 *, stop_distance: Decimal, target_distance: Decimal,
                 risk_budget: Decimal, capital_budget: Decimal,
                 fee_per_side: Decimal, hold: timedelta = timedelta(seconds=60)) -> tuple[Plan, ...]:
    if underlying not in {"SPY", "QQQ"} or underlying_direction not in {"LONG", "SHORT"}:
        raise ValueError("Explicit SPY/QQQ direction required")
    if target_distance not in {D("0.05"), D("0.10")}:
        raise ValueError("Declared option premium targets are 0.05 or 0.10")
    right = "CALL" if underlying_direction == "LONG" else "PUT"
    candidates = []
    for contract in contracts:
        if contract.engine != EngineId.OPTIONS or contract.underlying != underlying or contract.right != right:
            continue
        quote = quotes.get(contract.symbol)
        if quote is None:
            continue
        candidate = plan(contract, quote, now, side="LONG", stop_distance=stop_distance,
                         target_distance=target_distance, risk_budget=risk_budget,
                         capital_budget=capital_budget, fee_per_side=fee_per_side, hold=hold)
        if candidate:
            candidates.append(candidate)
    # A ranked alternatives list, not authorization to buy every candidate.
    return tuple(sorted(candidates, key=lambda p: (
        quotes[p.contract.symbol].ask - quotes[p.contract.symbol].bid,
        p.contract.expires_at, p.contract.symbol)))


def exit_reason(position: Plan, quote: Quote, now: datetime) -> str | None:
    if quote.symbol != position.contract.symbol or not quote.valid(now):
        return None
    price = quote.bid if position.side == "LONG" else quote.ask
    if (price <= position.stop if position.side == "LONG" else price >= position.stop):
        return "STOP"
    if (price >= position.target if position.side == "LONG" else price <= position.target):
        return "TARGET"
    if now >= position.deadline:
        return "MAX_HOLD"
    return None
