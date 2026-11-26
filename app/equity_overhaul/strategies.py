"""Three frozen long-only replacement hypotheses, shared by replay/observation.

Independent adaptations of documented rule families, not reproductions of
public performance claims. Signals have zero selection or execution authority.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from app.strategy_research.candidates import Bar
from .admission import CANDIDATES

EASTERN=ZoneInfo('America/New_York')
RULES={
 'ORB5_LONG_V1':dict(owner='WARRIOR',target_r='2',max_hold=60,max_trades=1,
    rules='Bullish first5; first close cross OR high; above session VWAP; 09:35-10:30; stop OR low*.9995'),
 'NOISE_BAND_LONG_V1':dict(owner='WARRIOR',target_r=None,max_hold=390,max_trades=2,
    rules='SPY/QQQ; 14 prior complete session same-minute mean absolute open move; max(open,priorclose)*(1+sigma); above VWAP; 30minute decisions through15:30; stop prior5 lows*.9995'),
 'VWAP_RECLAIM_SCALP_V1':dict(owner='SCALPER',target_r='1',max_hold=5,max_trades=3,
    rules='09:45-15:30; prior bar tags VWAP; bullish current close above prior high and rising SMA20; rising VWAP; stop last3 lows*.9995'),
}


def rule_fingerprint(policy):
    if policy not in RULES:raise ValueError('unknown replacement policy')
    return sha256(json.dumps(RULES[policy],sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ReplacementSignal:
    policy_id: str
    symbol: str
    at: object
    stop: D
    target_r: D | None
    max_hold: int
    execution_authorized: bool=False


def _sessions(history,at):
    if not history or len(history)>22*390:raise ValueError('bounded nonempty minute history required')
    if at.utcoffset() is None:raise ValueError('aware decision cutoff required')
    symbol=history[0].symbol
    groups=[]
    for b in history:
        if b.symbol!=symbol or any(x.utcoffset() is None for x in (b.opened,b.closed,b.session_end)):
            raise ValueError('one symbol and aware timestamps required')
        if not b.opened<b.closed<=min(b.session_end,at) or b.closed-b.opened!=timedelta(minutes=1):
            raise ValueError('completed one-minute bars required')
        local=b.opened.astimezone(EASTERN)
        if local.second or local.microsecond or local.hour*60+local.minute<570:
            raise ValueError('aligned regular-session bars required')
        if any(not isinstance(x,D) or not x.is_finite() for x in (b.open,b.high,b.low,b.close,b.volume)):
            raise ValueError('finite Decimal OHLCV required')
        if b.low<=0 or b.volume<0 or not b.low<=min(b.open,b.close)<=max(b.open,b.close)<=b.high:
            raise ValueError('invalid OHLCV')
        if not groups or groups[-1][-1].session_end!=b.session_end:
            if groups and (groups[-1][-1].closed!=groups[-1][-1].session_end or b.opened<=groups[-1][-1].closed):
                raise ValueError('prior session incomplete or unordered')
            if local.hour*60+local.minute!=570:raise ValueError('missing opening minute')
            groups.append([])
        elif groups[-1][-1].closed!=b.opened:
            raise ValueError('missing or duplicate minute')
        groups[-1].append(b)
    return groups


def vwap(bars):
    volume=sum((b.volume for b in bars),D(0))
    return sum(((b.high+b.low+b.close)/3*b.volume for b in bars),D(0))/volume if volume>0 else None


def signal(history,policy,*,at):
    if policy not in CANDIDATES:raise ValueError('unknown replacement policy')
    groups=_sessions(history,at);bars=groups[-1];b=bars[-1]
    clock=b.closed.astimezone(EASTERN);minute=clock.hour*60+clock.minute
    vw=vwap(bars)
    if vw is None or b.closed>=b.session_end:return None
    triggered=False
    if policy=='ORB5_LONG_V1':
        if len(bars)<6 or not 575<=minute<=630:return None
        level=max(x.high for x in bars[:5])
        triggered=(bars[4].close>bars[0].open and b.close>level and bars[-2].close<=level and b.close>vw)
        stop=min(x.low for x in bars[:5])*D('.9995')
    elif policy=='NOISE_BAND_LONG_V1':
        if b.symbol not in {'SPY','QQQ'} or len(groups)<15 or len(bars)%30 or minute>930:return None
        index=len(bars)-1
        earlier=groups[-15:-1]
        if any(len(g)<=index for g in earlier):return None
        sigma=sum((abs(g[index].close/g[0].open-1) for g in earlier),D(0))/14
        boundary=max(bars[0].open,earlier[-1][-1].close)*(1+sigma)
        triggered=b.close>max(vw,boundary)
        stop=min(x.low for x in bars[-5:])*D('.9995')
    else:
        if len(bars)<22 or not 585<=minute<=930:return None
        prior=bars[-2];pv=vwap(bars[:-1])
        slow=sum((x.close for x in bars[-20:]),D(0))/20
        prior_slow=sum((x.close for x in bars[-21:-1]),D(0))/20
        triggered=(pv is not None and vw>=pv and slow>prior_slow and prior.low<=pv
                   and b.close>max(vw,prior.high,b.open))
        stop=min(x.low for x in bars[-3:])*D('.9995')
    if not triggered or not 0<stop<b.close:return None
    rule=RULES[policy]
    return ReplacementSignal(policy,b.symbol,b.closed,stop,D(rule['target_r']) if rule['target_r'] else None,rule['max_hold'])


def exit_due(history,policy,*,at):
    groups=_sessions(history,at);bars=groups[-1]
    if policy!='NOISE_BAND_LONG_V1':return False
    if len(bars)%30:return False
    # Re-evaluate at the same completed-bar clock as entries; no intrabar fill
    # at a subsequently calculated VWAP or historical same-minute boundary.
    return signal(history,policy,at=at) is None
