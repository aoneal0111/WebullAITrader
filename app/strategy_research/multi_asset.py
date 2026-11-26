"""Frozen cross-market research candidates, structure checks and review gate.

These functions cannot create orders, select production policies, allocate
capital or activate an engine. Literature motivates families, not Atlas rates.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal as D
import re


@dataclass(frozen=True)
class ResearchProfile:
    market: str
    candidate: str
    evidence: str
    required_data: tuple[str, ...]
    implementation: str
    execution_authorized: bool = False

    def __post_init__(self):
        if self.execution_authorized:
            raise ValueError('Research profiles cannot grant execution authority')


PROFILES = (
    ResearchProfile('EQUITY_MOMENTUM', 'OPENING_RANGE_RETEST',
        'Stocks-in-play ORB literature; retest is an unvalidated Atlas adaptation',
        ('point-in-time stock universe', 'complete regular-session minute bars', 'executable quotes'),
        'app.strategy_research.candidates'),
    ResearchProfile('SCALPER', 'TREND_VWAP_PULLBACK',
        'Experimental execution-cost-aware pullback; no verified high win rate',
        ('complete session minute bars', 'timestamped bid/ask and sizes', 'fees and latency'),
        'app.strategy_research.candidates'),
    ResearchProfile('CRYPTO', 'BTC_ETH_TREND_PULLBACK_1H_4H',
        'Crypto time-series momentum literature; hourly pullback is an Atlas adaptation',
        ('venue-specific BTC/USD and ETH/USD hourly bars', 'completed four-hour bars', 'spot fees and quotes'),
        'app.strategy_research.multi_asset.crypto_pullback'),
    ResearchProfile('FUTURES', 'CONTRACT_TREND_PULLBACK_5M',
        'Futures trend-following literature; five-minute timing is an Atlas adaptation',
        ('actual dated-contract five-minute bars', 'tick and multiplier', 'roll, margin and session calendar', 'fees and quotes'),
        'app.strategy_research.multi_asset.futures_pullback'),
    ResearchProfile('OPTIONS', 'CASH_SETTLED_PUT_CREDIT_SPREAD',
        'Cboe defined-risk premium benchmarks and OCC spread mechanics; no Atlas win-rate validation',
        ('historical synchronized option-leg bid/ask', 'European cash-settlement identity',
         'expiry settlement values', 'multi-leg fees and closing quotes'),
        'app.strategy_research.multi_asset.PutCreditSpread'),
)


@dataclass(frozen=True)
class Candle:
    end: datetime
    high: D
    low: D
    close: D

    def __post_init__(self):
        _aware(self.end)
        _numbers(self.high, self.low, self.close)
        if not 0 < self.low <= self.close <= self.high:
            raise ValueError('Positive finite OHLC geometry required')


def _aware(at):
    if not isinstance(at, datetime) or at.utcoffset() is None:
        raise ValueError('Aware timestamp required')


def _numbers(*values):
    if any(not isinstance(v, D) or not v.is_finite() for v in values):
        raise ValueError('Finite Decimal values required')


def _history(bars, at, spacing):
    _aware(at)
    if not isinstance(bars, tuple) or not 22 <= len(bars) <= 512 or any(not isinstance(b, Candle) for b in bars):
        raise ValueError('22 to 512 typed completed bars required')
    if any(b.end > at for b in bars):
        raise ValueError('Future bar rejected')
    if any(b.end-a.end != spacing for a, b in zip(bars, bars[1:])):
        raise ValueError('Contiguous bars required')
    if any(b.end.timestamp() % spacing.total_seconds() for b in bars):
        raise ValueError('UTC-aligned completed bars required')
    if not timedelta(0) <= at-bars[-1].end < spacing:
        raise ValueError('Stale history rejected')


def _ema(bars, period):
    result = bars[0].close
    alpha = D(2) / (period+1)
    for b in bars[1:]:
        result += alpha*(b.close-result)
    return result


@dataclass(frozen=True)
class ResearchSetup:
    candidate: str
    symbol: str
    side: str
    signal_at: datetime
    reference: D
    stop: D
    execution_authorized: bool = False

    def __post_init__(self):
        _aware(self.signal_at)
        _numbers(self.reference, self.stop)
        if (self.execution_authorized or self.side not in {'LONG', 'SHORT'}
                or min(self.reference, self.stop) <= 0
                or (self.reference-self.stop)*(1 if self.side == 'LONG' else -1) <= 0):
            raise ValueError('Research-only setup with valid structural risk required')


def _pullback(bars):
    fast, slow = _ema(bars, 8), _ema(bars, 21)
    prior_slow = _ema(bars[:-1], 21)
    a, b, c = bars[-3:]
    if fast > slow > prior_slow and b.close < a.close and c.close > b.high:
        return 'LONG', min(x.low for x in bars[-3:])
    if fast < slow < prior_slow and b.close > a.close and c.close < b.low:
        return 'SHORT', max(x.high for x in bars[-3:])
    return None


def crypto_pullback(symbol, hourly, four_hourly, *, at):
    """Spot-only long structure; no leverage, funding, quotes or fill claims."""
    if symbol not in {'BTC/USD', 'ETH/USD'}:
        raise ValueError('Frozen BTC/USD and ETH/USD research universe required')
    _history(hourly, at, timedelta(hours=1))
    # Higher-timeframe evidence must exist by the signal, not merely by a
    # later invocation time. Strict freshness also rejects unfinished candles.
    _history(four_hourly, hourly[-1].end, timedelta(hours=4))
    hit = _pullback(hourly)
    if hit and hit[0] == 'LONG' and _ema(four_hourly, 8) > _ema(four_hourly, 21):
        return ResearchSetup(PROFILES[2].candidate, symbol, 'LONG', hourly[-1].end,
                             hourly[-1].close, hit[1])
    return None


def futures_pullback(symbol, bars, *, at, tick, expires_at, session_start, session_end, multiplier):
    """Actual-contract intraday hypothesis; never splice continuous prices.

    Gaps across maintenance/session breaks require new warmup. Tick/multiplier
    and expiry are data requirements, not a substitute for a roll/margin model.
    """
    _numbers(tick, multiplier)
    _aware(expires_at)
    _aware(session_start)
    _aware(session_end)
    if (not isinstance(symbol, str) or not re.fullmatch(r'[A-Z]{1,5}[FGHJKMNQUVXZ][0-9]{1,4}', symbol)
            or tick <= 0 or multiplier <= 0):
        raise ValueError('Actual dated contract, positive tick and multiplier required')
    _history(bars, at, timedelta(minutes=5))
    if not session_start < bars[0].end <= at < session_end:
        raise ValueError('History must belong to the declared contract session')
    if at+timedelta(minutes=15) >= min(expires_at, session_end):
        return None
    if any(v % tick for b in bars for v in (b.high, b.low, b.close)):
        raise ValueError('Contract tick alignment required')
    hit = _pullback(bars)
    if hit:
        side, structure = hit
        stop = structure-tick if side == 'LONG' else structure+tick
        return ResearchSetup(PROFILES[3].candidate, symbol, side, bars[-1].end, bars[-1].close, stop)
    return None


@dataclass(frozen=True)
class PutLeg:
    identity: str
    underlying: str
    expiry: datetime
    settlement_id: str
    strike: D
    bid: D
    ask: D
    quoted_at: datetime
    multiplier: D = D(100)
    exercise: str = 'EUROPEAN'
    settlement: str = 'CASH'

    def __post_init__(self):
        _aware(self.expiry)
        _aware(self.quoted_at)
        _numbers(self.strike, self.bid, self.ask, self.multiplier)
        if (not self.identity or not self.settlement_id or self.underlying not in {'SPX', 'XSP'}
                or self.multiplier != 100 or not 0 < self.strike
                or not 0 <= self.bid <= self.ask or self.ask == 0
                or self.exercise != 'EUROPEAN' or self.settlement != 'CASH'):
            raise ValueError('Explicit European cash-settled put leg required')


@dataclass(frozen=True)
class PutCreditSpread:
    """Quoted-side research valuation; no atomic multi-leg fill assumption.

    Maximum profit/loss are terminal-payoff bounds with reserved costs.
    Wide quotes/slippage can make an earlier quoted liquidation worse.
    """
    short: PutLeg
    long: PutLeg
    at: datetime
    fee_per_leg_side: D
    slippage_per_leg_side: D

    def __post_init__(self):
        _aware(self.at)
        _numbers(self.fee_per_leg_side, self.slippage_per_leg_side)
        if (not isinstance(self.short, PutLeg) or not isinstance(self.long, PutLeg)
                or min(self.fee_per_leg_side, self.slippage_per_leg_side) < 0):
            raise ValueError('Typed legs and explicit nonnegative costs required')
        s, l = self.short, self.long
        if (s.identity == l.identity or (s.underlying, s.expiry, s.multiplier, s.settlement_id)
                != (l.underlying, l.expiry, l.multiplier, l.settlement_id)
                or s.strike <= l.strike or self.at >= s.expiry):
            raise ValueError('Matching settlement/expiry and protective lower strike required')
        if any(not timedelta(0) <= self.at-leg.quoted_at <= timedelta(seconds=2) for leg in (s, l)):
            raise ValueError('Fresh simultaneous executable leg quotes required')
        if not 0 < self.credit < s.strike-l.strike:
            raise ValueError('Positive executable credit below spread width required')
        if self.maximum_profit <= 0:
            raise ValueError('Credit must cover round-trip costs')

    @property
    def execution_authorized(self):
        return False

    @property
    def credit(self):
        return self.short.bid-self.long.ask-2*self.slippage_per_leg_side

    @property
    def maximum_profit(self):
        # Conservatively reserve both entry and closing fees/slippage even
        # when evaluating terminal payoff; this is not a historical fill model.
        return (self.credit-2*self.slippage_per_leg_side)*self.short.multiplier-4*self.fee_per_leg_side

    @property
    def maximum_loss(self):
        return (self.short.strike-self.long.strike)*self.short.multiplier-self.maximum_profit

    @property
    def terminal_two_outcome_breakeven(self):
        return self.maximum_loss/(self.maximum_loss+self.maximum_profit)

    def terminal_pnl(self, official_settlement):
        _numbers(official_settlement)
        if official_settlement < 0:
            raise ValueError('Nonnegative official settlement required')
        liability = max(self.short.strike-official_settlement, D(0))-max(self.long.strike-official_settlement, D(0))
        return self.maximum_profit-liability*self.short.multiplier

    def closing_pnl(self, short_close, long_close, *, at):
        _aware(at)
        for original, quote in ((self.short, short_close), (self.long, long_close)):
            if (not isinstance(quote, PutLeg) or quote.identity != original.identity
                    or (quote.underlying, quote.strike, quote.expiry, quote.settlement_id)
                    != (original.underlying, original.strike, original.expiry, original.settlement_id)
                    or not self.at <= quote.quoted_at <= at < original.expiry
                    or at-quote.quoted_at > timedelta(seconds=2)):
                raise ValueError('Fresh matching closing quotes before expiry required')
        debit = short_close.ask-long_close.bid+2*self.slippage_per_leg_side
        return (self.credit-debit)*self.short.multiplier-4*self.fee_per_leg_side
