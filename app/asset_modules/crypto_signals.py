"""Offline spot BUY candidate: completed range breakout and EMA agreement."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal as D, ROUND_FLOOR, localcontext
from hashlib import sha256

from app.asset_modules.contract_logic import Bar, direction
from app.asset_modules.contract_worker_codec import _decimal, _symbol

POLICY = 'CRYPTO_RANGE_EMA_PAPER_V1'


@dataclass(frozen=True)
class CryptoHistory:
    symbol: str
    bars: tuple[Bar, ...]


@dataclass(frozen=True)
class CryptoQuote:
    symbol: str
    bid: D
    ask: D
    timestamp: datetime


@dataclass(frozen=True)
class CryptoSignalSettings:
    notional_cap: D = D(200)
    capital_budget: D = D(250)
    risk_budget: D = D(25)
    fee: D = D('.001')
    slippage: D = D('.001')
    target_r: D = D(3)
    max_extension_r: D = D('.5')

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, D): raise ValueError('Decimal signal settings required')
            _decimal(str(value))
        if (not 0 < self.notional_cap <= 250 or not 0 < self.capital_budget <= 10000
                or not 0 < self.risk_budget <= 25 or not 0 <= self.fee <= D('.01')
                or not 0 <= self.slippage <= D('.01') or not 2 <= self.target_r <= 5
                or not 0 < self.max_extension_r <= 1):
            raise ValueError('Bounded crypto signal settings required')


def propose(history, quote, now, settings):
    if not isinstance(history, CryptoHistory) or not isinstance(settings, CryptoSignalSettings):
        raise ValueError('Typed crypto history and settings required')
    symbol = _symbol(history.symbol)
    if not symbol.endswith('/USD') or symbol.count('/') != 1:
        raise ValueError('Canonical spot USD pair required')
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Aware decision timestamp required')
    bars = history.bars
    if not isinstance(bars, tuple) or len(bars) > 48 or any(not isinstance(b, Bar) for b in bars):
        raise ValueError('At most 48 typed completed bars required')
    if len(bars) < 22: return None, 'INSUFFICIENT_HISTORY'
    if any(b.end > now for b in bars) or any(a.end >= b.end for a,b in zip(bars,bars[1:])):
        raise ValueError('Completed ordered bars required')
    if any(b.end-a.end != timedelta(minutes=1) for a,b in zip(bars,bars[1:])) or now-bars[-1].end > timedelta(minutes=1):
        return None, 'HISTORY_NOT_CURRENT_OR_CONTIGUOUS'
    for bar in bars:
        for value in (bar.high, bar.low, bar.close): _decimal(str(value))
    if quote is None: return None, 'QUOTE_UNAVAILABLE'
    if not isinstance(quote, CryptoQuote): raise ValueError('Typed crypto quote required')
    for price in (quote.bid, quote.ask):
        if not isinstance(price, D): raise ValueError('Decimal quote required')
        _decimal(str(price))
    if (quote.symbol != symbol or not isinstance(quote.timestamp, datetime)
            or quote.timestamp.tzinfo is None or quote.timestamp.utcoffset() is None
            or not 0 < quote.bid <= quote.ask or not timedelta(0) <= now-quote.timestamp <= timedelta(seconds=2)
            or quote.timestamp < bars[-1].end):
        return None, 'QUOTE_UNAVAILABLE'
    with localcontext() as ctx:
        ctx.prec = 80
        if direction(bars, now) != 'LONG': return None, 'NO_CONFIRMED_SPOT_BREAKOUT'
        trigger = max(b.high for b in bars[-21:-1])
        stop = min(b.low for b in bars[-6:-1])
        if quote.bid < trigger: return None, 'TRIGGER_NOT_CURRENT'
        if trigger <= stop or quote.ask <= stop: return None, 'INVALID_STRUCTURE'
        if quote.ask-trigger > settings.max_extension_r*(trigger-stop): return None, 'ENTRY_EXTENDED'
        fill = quote.ask*(1+settings.slippage)
        target = (fill+settings.target_r*(fill-stop)).quantize(D('.00000001'), rounding=ROUND_FLOOR)
        risk = fill*(1+settings.fee)-stop*(1-settings.slippage)*(1-settings.fee)
        reward = target*(1-settings.slippage)*(1-settings.fee)-fill*(1+settings.fee)
        if reward < risk: return None, 'NET_REWARD_INSUFFICIENT'
        notional = min(settings.notional_cap, settings.capital_budget/(1+settings.fee),
            settings.risk_budget/risk*fill)-fill*D('.00000001')
        notional = notional.quantize(D('.00000001'), rounding=ROUND_FLOOR)
        if notional <= 0 or notional/fill < D('.00000001'): return None, 'BUDGET_INSUFFICIENT'
        identity = 'CRYPTO_SIGNAL|'+sha256('|'.join((POLICY,symbol,bars[-1].end.isoformat(),'BUY')).encode()).hexdigest()
        return {'id':identity,'symbol':symbol,'action':'BUY','notional':str(notional),
                'stop':str(stop),'target':str(target),'reason':POLICY}, 'PROPOSED'
