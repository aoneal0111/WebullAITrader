"""Completed-bar futures PAPER proposals; supplied data only, no network or AI."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal as D, ROUND_FLOOR
from hashlib import sha256

from app.asset_modules.contract_logic import Bar, Contract, Plan, Quote, direction
from app.asset_modules.contract_serialization import number
from app.asset_modules.engine_catalog import EngineId

POLICY = 'FUTURES_RANGE_EMA_PAPER_V1'


@dataclass(frozen=True)
class FuturesHistory:
    symbol: str
    bars: tuple[Bar, ...]


@dataclass(frozen=True)
class FuturesSignalSettings:
    risk_budget: D = D('25')
    capital_budget: D = D('2500')
    fee_per_side: D = D('1')
    max_extension_r: D = D('0.50')
    target_r: D = D('3')
    slippage_ticks: int = 1
    max_contracts: int = 5
    hold: timedelta = timedelta(minutes=15)

    def __post_init__(self):
        for value in (self.risk_budget, self.capital_budget, self.max_extension_r, self.target_r):
            number(value, positive=True)
        number(self.fee_per_side)
        if (self.max_extension_r > 1 or not 2 <= self.target_r <= 5
                or type(self.slippage_ticks) is not int or not 0 <= self.slippage_ticks <= 10
                or type(self.max_contracts) is not int or not 1 <= self.max_contracts <= 5
                or not isinstance(self.hold, timedelta) or not timedelta(0) < self.hold <= timedelta(hours=1)):
            raise ValueError('Bounded PAPER signal settings required')


@dataclass(frozen=True)
class FuturesProposal:
    intent_id: str
    signal_at: datetime
    trigger: D
    plan: Plan


def propose(contract: Contract, history: FuturesHistory, quote: Quote, now: datetime,
            settings: FuturesSignalSettings) -> tuple[FuturesProposal | None, str]:
    if not isinstance(contract, Contract) or contract.engine is not EngineId.FUTURES:
        raise ValueError('Explicit futures contract required')
    if not isinstance(history, FuturesHistory) or history.symbol != contract.symbol:
        raise ValueError('History must belong to the actual futures contract')
    if (not isinstance(history.bars, tuple) or len(history.bars) > 256
            or any(not isinstance(b, Bar) for b in history.bars)):
        raise ValueError('Bounded typed completed-bar history required')
    if not isinstance(settings, FuturesSignalSettings):
        raise ValueError('Explicit signal settings required')
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Aware decision time required')
    bars = history.bars
    if len(bars) < 22:
        return None, 'INSUFFICIENT_HISTORY'
    if any(b.end > now for b in bars) or any(a.end >= b.end for a, b in zip(bars, bars[1:])):
        raise ValueError('History must be completed and strictly ordered')
    if (any(b.end-a.end != timedelta(minutes=1) for a, b in zip(bars, bars[1:]))
            or now-bars[-1].end > timedelta(minutes=1)):
        return None, 'HISTORY_NOT_CURRENT_OR_CONTIGUOUS'
    if any(v % contract.tick for b in bars for v in (b.high, b.low, b.close)):
        return None, 'BAR_TICK_MISMATCH'
    side = direction(bars, now)
    if side is None:
        return None, 'NO_CONFIRMED_BREAKOUT'
    if (not isinstance(quote, Quote) or quote.symbol != contract.symbol or not quote.valid(now)
            or quote.source_at < bars[-1].end or quote.bid % contract.tick or quote.ask % contract.tick):
        return None, 'QUOTE_UNAVAILABLE'
    if now+settings.hold >= contract.expires_at:
        return None, 'CONTRACT_EXPIRING'
    sign = D(1) if side == 'LONG' else D(-1)
    prior = bars[-21:-1]
    trigger = (max(b.high for b in prior)+contract.tick if side == 'LONG'
               else min(b.low for b in prior)-contract.tick)
    # Executable side must still confirm the breakout, not just a historical close.
    confirmation = quote.bid if side == 'LONG' else quote.ask
    if sign*(confirmation-trigger) < 0:
        return None, 'TRIGGER_NOT_CURRENT'
    stop = (min(b.low for b in bars[-6:-1])-contract.tick if side == 'LONG'
            else max(b.high for b in bars[-6:-1])+contract.tick)
    structural_risk = sign*(trigger-stop)
    entry = quote.ask if side == 'LONG' else quote.bid
    if stop <= 0 or structural_risk <= 0:
        return None, 'INVALID_STRUCTURE'
    if sign*(entry-trigger) > settings.max_extension_r*structural_risk:
        return None, 'ENTRY_EXTENDED'
    distance = sign*(entry-stop)
    target_ticks = (distance*settings.target_r/contract.tick).to_integral_value(rounding=ROUND_FLOOR)
    target = entry+sign*target_ticks*contract.tick
    slip = contract.tick*settings.slippage_ticks
    fill = entry+sign*slip
    if min(fill, target) <= 0:
        return None, 'NONPOSITIVE_PRICE'
    per_risk = (distance+2*slip)*contract.multiplier+2*settings.fee_per_side
    per_reward = (sign*(target-entry)-2*slip)*contract.multiplier-2*settings.fee_per_side
    if per_reward < per_risk:
        return None, 'NET_REWARD_INSUFFICIENT'
    quantity = int(min(settings.risk_budget/per_risk,
                       settings.capital_budget/(contract.margin+2*settings.fee_per_side),
                       D(settings.max_contracts)).to_integral_value(rounding=ROUND_FLOOR))
    if quantity < 1:
        return None, 'BUDGET_INSUFFICIENT'
    p = Plan(contract, side, quantity, entry, stop, target, settings.fee_per_side,
             now, now+settings.hold, POLICY)
    # One successful intent per contract/direction/completed signal bar, across restarts.
    key = '|'.join((POLICY, contract.symbol, contract.expires_at.isoformat(),
                    bars[-1].end.isoformat(), side))
    identity = 'FUTURES|' + sha256(key.encode()).hexdigest()
    return FuturesProposal(identity, bars[-1].end, trigger, p), 'PROPOSED'


class FuturesPaperController:
    """Dedicated offline ingress. Trusted authorization owns session/halt policy.

    This is not the shared equity router or a broker adapter. The ledger retains
    final sizing, quote, cash, margin, duplicate and protection authority.
    """
    def __init__(self, ledger, *, settings, authorize, entry_router=None):
        from app.asset_modules.futures_paper import FuturesPaper
        if not isinstance(ledger, FuturesPaper) or not isinstance(settings, FuturesSignalSettings):
            raise ValueError('Futures PAPER ledger and settings required')
        if not callable(authorize):
            raise ValueError('Trusted deterministic authorization callback required')
        if settings.slippage_ticks != ledger.slippage_ticks:
            raise ValueError('Signal and ledger slippage must agree')
        if settings.max_contracts > ledger.max_contracts:
            raise ValueError('Signal contract cap exceeds ledger cap')
        if entry_router is not None:
            from app.asset_modules.futures_allocation import FuturesAllocationRouter
            if not isinstance(entry_router,FuturesAllocationRouter) or entry_router.ledger is not ledger:
                raise ValueError('Router must own this futures ledger')
        self.ledger, self.settings, self.authorize = ledger, settings, authorize
        self.entry_router = entry_router
        self.last_allocation_reconciliation = {}

    def protect(self, quotes, *, now):
        results = self.ledger.protect(quotes,now=now)
        if self.entry_router is not None:
            self.last_allocation_reconciliation = self.entry_router.reconcile()
        return results

    def step(self, contract, history, quotes, *, now):
        with self.ledger.lock:
            return self._step(contract, history, quotes, now=now)

    def _step(self, contract, history, quotes, *, now):
        # Existing exits run first and do not depend on authorization or new signals.
        protection = self.protect(quotes, now=now)
        candidate, reason = propose(contract, history, quotes.get(contract.symbol), now, self.settings)
        if candidate is None:
            return {'entry': reason, 'protection': protection, 'proposal': None}
        if self.ledger.intent_state(candidate.intent_id) is not None:
            result = 'DUPLICATE_SIGNAL'
        elif not self.ledger.enabled:
            result = 'ENTRIES_DISABLED'
        elif self.authorize(candidate, now) is not True:
            result = 'ENTRY_NOT_AUTHORIZED'
        elif self.entry_router is not None:
            result = self.entry_router.submit(candidate,quotes[contract.symbol],now=now)
        else:
            result = self.ledger.enter(candidate.intent_id, candidate.plan,
                                       quotes[contract.symbol], now=now)
        return {'entry': result, 'protection': protection, 'proposal': candidate}
