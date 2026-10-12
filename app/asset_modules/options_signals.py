"""Offline SPY/QQQ direction-to-premium proposals; supplied data, no broker/AI."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal as D, ROUND_FLOOR
from hashlib import sha256

from app.asset_modules.contract_logic import Bar, Contract, Plan, Quote, direction
from app.asset_modules.contract_serialization import number
from app.asset_modules.engine_catalog import EngineId

POLICY = 'OPTIONS_RANGE_EMA_PAPER_V1'


@dataclass(frozen=True)
class OptionsHistory:
    underlying: str
    bars: tuple[Bar, ...]


@dataclass(frozen=True)
class OptionsSignalSettings:
    risk_budget: D = D('25')
    capital_budget: D = D('2500')
    fee_per_side: D = D('0.65')
    slippage: D = D('0.01')
    stop_distance: D = D('0.02')
    target_distance: D = D('0.10')
    max_contracts: int = 5
    hold: timedelta = timedelta(seconds=60)

    def __post_init__(self):
        for v in (self.risk_budget, self.capital_budget, self.stop_distance, self.target_distance):
            number(v, positive=True)
        number(self.fee_per_side)
        number(self.slippage)
        if (self.target_distance not in {D('0.05'), D('0.10')}
                or type(self.max_contracts) is not int or not 1 <= self.max_contracts <= 5
                or not isinstance(self.hold, timedelta)
                or not timedelta(0) < self.hold <= timedelta(seconds=60)):
            raise ValueError('Bounded premium scalp settings required')


@dataclass(frozen=True)
class OptionsProposal:
    intent_id: str
    signal_at: datetime
    underlying_direction: str
    plan: Plan


def propose(history, underlying_quote, contracts, quotes, now, settings):
    """Rank alternatives; buying calls/puts always means LONG option premium."""
    if (not isinstance(history, OptionsHistory) or history.underlying not in {'SPY', 'QQQ'}
            or not isinstance(history.bars, tuple) or len(history.bars) > 256
            or any(not isinstance(b, Bar) for b in history.bars)):
        raise ValueError('Bounded typed SPY/QQQ history required')
    if not isinstance(settings, OptionsSignalSettings):
        raise ValueError('Explicit option signal settings required')
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Aware decision timestamp required')
    if (not isinstance(contracts, tuple) or len(contracts) > 32
            or any(not isinstance(c, Contract) or c.engine is not EngineId.OPTIONS for c in contracts)
            or len({c.symbol for c in contracts}) != len(contracts)):
        raise ValueError('At most 32 distinct supplied option contracts required')
    bars = history.bars
    if len(bars) < 22:
        return (), 'INSUFFICIENT_HISTORY'
    if any(b.end > now for b in bars) or any(a.end >= b.end for a, b in zip(bars, bars[1:])):
        raise ValueError('Completed strictly ordered bars required')
    if (any(b.end-a.end != timedelta(minutes=1) for a, b in zip(bars, bars[1:]))
            or now-bars[-1].end > timedelta(minutes=1)):
        return (), 'HISTORY_NOT_CURRENT_OR_CONTIGUOUS'
    side = direction(bars, now)
    if side is None:
        return (), 'NO_CONFIRMED_BREAKOUT'
    q = underlying_quote
    if (not isinstance(q, Quote) or q.symbol != history.underlying or not q.valid(now)
            or q.source_at < bars[-1].end):
        return (), 'UNDERLYING_QUOTE_UNAVAILABLE'
    prior = bars[-21:-1]
    if ((side == 'LONG' and q.bid <= max(b.high for b in prior))
            or (side == 'SHORT' and q.ask >= min(b.low for b in prior))):
        return (), 'TRIGGER_NOT_CURRENT'
    right = 'CALL' if side == 'LONG' else 'PUT'
    # Identity belongs to the underlying signal, not one strike alternative.
    key = '|'.join((POLICY, history.underlying, bars[-1].end.isoformat(), side))
    identity = 'OPTIONS|' + sha256(key.encode()).hexdigest()
    candidates = []
    for c in contracts:
        if c.underlying != history.underlying or c.right != right or c.multiplier != D(100):
            continue
        quote = quotes.get(c.symbol)
        if (not isinstance(quote, Quote) or quote.symbol != c.symbol or not quote.valid(now)
                or quote.source_at < bars[-1].end or now+settings.hold >= c.expires_at):
            continue
        entry, stop, target = quote.ask, quote.ask-settings.stop_distance, quote.ask+settings.target_distance
        if (stop <= 0 or any(v % c.tick for v in
                (quote.bid, entry, stop, target, settings.slippage))):
            continue
        # Same conservative stop-fill and cost formulas as the final ledger.
        risk = (settings.stop_distance+2*settings.slippage)*c.multiplier+2*settings.fee_per_side
        reward = (settings.target_distance-2*settings.slippage)*c.multiplier-2*settings.fee_per_side
        if reward < risk:
            continue
        capital = (entry+settings.slippage)*c.multiplier+2*settings.fee_per_side
        quantity = int(min(settings.risk_budget/risk, settings.capital_budget/capital,
                           D(settings.max_contracts)).to_integral_value(rounding=ROUND_FLOOR))
        if quantity < 1:
            continue
        p = Plan(c, 'LONG', quantity, entry, stop, target, settings.fee_per_side,
                 now, now+settings.hold, POLICY)
        candidates.append(OptionsProposal(identity, bars[-1].end, side, p))
    candidates.sort(key=lambda x: (quotes[x.plan.contract.symbol].ask-quotes[x.plan.contract.symbol].bid,
                                   x.plan.contract.expires_at, x.plan.contract.symbol))
    return tuple(candidates), 'PROPOSED' if candidates else 'NO_ELIGIBLE_PREMIUM_PLAN'


class OptionsPaperController:
    """Dedicated offline ingress; exits precede signals and entry authorization.

    Trusted authorization must check session/halt/tradability. This controller
    has no shared allocation, network, broker, GUI or Grok composition yet.
    """
    def __init__(self, ledger, *, settings, authorize):
        from app.asset_modules.options_paper import OptionsPaper
        if not isinstance(ledger, OptionsPaper) or not isinstance(settings, OptionsSignalSettings):
            raise ValueError('Options PAPER ledger and settings required')
        if not callable(authorize):
            raise ValueError('Trusted authorization callback required')
        if settings.slippage != ledger.slippage or settings.max_contracts > ledger.max_contracts:
            raise ValueError('Signal and ledger execution settings must agree')
        self.ledger, self.settings, self.authorize = ledger, settings, authorize

    def protect(self, quotes, *, now):
        return self.ledger.protect(quotes, now=now)

    def step(self, history, underlying_quote, contracts, quotes, *, now):
        with self.ledger.lock:
            protection = self.protect(quotes, now=now)
            candidates, reason = propose(history, underlying_quote, contracts, quotes, now, self.settings)
            candidate = candidates[0] if candidates else None
            if candidate is None:
                result = reason
            elif self.ledger.has_intent(candidate.intent_id):
                result = 'DUPLICATE_SIGNAL'
            elif not self.ledger.enabled:
                result = 'ENTRIES_DISABLED'
            elif self.authorize(candidate, now) is not True:
                result = 'ENTRY_NOT_AUTHORIZED'
            else:
                # Submit just the first ranked alternative; refusals do not try others.
                result = self.ledger.enter(candidate.intent_id, candidate.plan,
                    quotes[candidate.plan.contract.symbol], now=now)
            return {'entry': result, 'protection': protection, 'proposal': candidate}
