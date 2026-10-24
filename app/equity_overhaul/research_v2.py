"""Two fixed post-V1 research hypotheses; no production signal/order authority.

Independent mathematical expressions informed by primary ORB/noise-band work.
These are adaptations, not replications of author performance or universes.
"""
from decimal import Decimal as D
from hashlib import sha256
from itertools import groupby
import json
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')
POLICIES = {
    'ORB5_ACTIVITY_RUNNER_V2': {
        'symbols': ['AAPL','MSFT','NVDA','TSLA'], 'selection': 'TOP_TWO_OPENING5_RELATIVE_VOLUME_PRIOR14',
        'entry': 'Bullish first5; stop-market above opening high from 09:35 until penultimate bar',
        'stop': 'Opening low times 0.9995; fixed before entry',
        'exit': 'Hard stop or same-session close; no profit target or 60-bar timeout',
        'max_entries_per_symbol_session': 1,
        'differences': 'Four fixed current stocks; rank top2 without RVOL>1 gate; structural stop instead of ATR; long-only; no portfolio sizing',
    },
    'NOISE_BAND_CLOCK_V2': {
        'symbols': ['SPY','QQQ'], 'selection': 'FIXED_ETFS_SPY_PRIMARY_QQQ_EXTENSION',
        'entry': 'Completed 30-minute clock; close above prior14 same-minute band and session VWAP; next-open entry',
        'stop': 'Lowest session price known at signal times 0.9995; fixed emergency stop',
        'exit': 'Next open after a 30-minute close fails band/VWAP; hard stop or same-session close; no target',
        'max_entries_per_symbol_session': 12,
        'differences': 'Long-only, no leverage/volatility targeting/overnight reversal; added session-low hard stop; no dividends adjustment; next-open fills',
    },
}
COSTS = [('frictionless_diagnostic','0','0'),('low_cost_diagnostic','1','0.0035'),
         ('normal','5','0.0035'),('stress','10','0.007')]


def experiment_plan():
    return {'version':'EQUITY_POST_V1_RESEARCH_V2', 'evidence_kind':'EXPLORATORY_ALREADY_REVIEWED_DATES',
            'policies': json.loads(json.dumps(POLICIES)), 'costs_per_side': json.loads(json.dumps(COSTS)),
            'selection_frozen_before_v2_results': True, 'parameter_search': False,
            'entry_ambiguity': 'STOP_FIRST_IF_ENTRY_AND_EXIT_LEVELS_SHARE_A_CANDLE',
            'production_changes': False, 'execution_authorized': False, 'promotion':'NONE'}


def plan_hash():
    return sha256(json.dumps(experiment_plan(), sort_keys=True, separators=(',',':')).encode()).hexdigest()


def prepare(data):
    """Cache only causal session prefixes; later sessions never feed earlier features."""
    prepared = {}
    for symbol, bars in data.items():
        sessions = []
        for _, grouped in groupby(bars, key=lambda b:b.session_end):
            candles = tuple(grouped); weight = D(0); numerator = D(0); vw=[]; lows=[]
            for b in candles:
                weight += b.volume; numerator += (b.high+b.low+b.close)/3*b.volume
                vw.append(numerator/weight if weight else None)
                lows.append(min(lows[-1],b.low) if lows else b.low)
            sessions.append({'bars':candles, 'date':candles[0].opened.astimezone(EASTERN).date(),
                             'vwap':vw, 'lows':lows, 'opening_volume':sum((b.volume for b in candles[:5]),D(0))})
        by_day = {s['date']:s for s in sessions}
        for i, s in enumerate(sessions):
            s['prior14'] = sessions[max(0,i-14):i]
        prepared[symbol] = by_day
    return prepared


def activity_selection(prepared, day):
    """One ranking at 09:35. No all-day volume or later returns are inspected."""
    ranks=[]
    for symbol in POLICIES['ORB5_ACTIVITY_RUNNER_V2']['symbols']:
        s=prepared.get(symbol,{}).get(day)
        if s is None or len(s['bars'])<6 or len(s['prior14'])<14 or s['opening_volume']<=0: continue
        average=sum((p['opening_volume'] for p in s['prior14']),D(0))/14
        if average<=0: continue
        ratio=s['opening_volume']/average
        ranks.append((symbol,ratio))
    return tuple(s for s,_ in sorted(ranks,key=lambda x:(-x[1],x[0]))[:2])


def noise_condition(s, j):
    bars=s['bars']; prior=s['prior14']; vw=s['vwap'][j]
    if (len(prior)!=14 or vw is None or (j+1)%30 or
            bars[j].closed>=bars[j].session_end or any(len(p['bars'])<=j for p in prior)):
        return False
    minute=bars[j].closed.astimezone(EASTERN).hour*60+bars[j].closed.astimezone(EASTERN).minute
    if minute>930:return False
    sigma=sum((abs(p['bars'][j].close/p['bars'][0].open-1) for p in prior),D(0))/14
    boundary=max(bars[0].open,prior[-1]['bars'][-1].close)*(1+sigma)
    return bars[j].close>max(boundary,vw)


def trades(prepared, policy, bps, fee, start, end):
    if policy not in POLICIES or not start<end or not D(0)<=bps<D(10000) or fee<0:
        raise ValueError('fixed V2 policy, costs and ordered dates required')
    result=[]; diagnostics={'eligible_symbol_sessions':0,'warmup_or_selection_exclusions':0}
    buy=1+bps/10000; sell=1-bps/10000
    for symbol in POLICIES[policy]['symbols']:
        for day,s in prepared.get(symbol,{}).items():
            if not start<=day<end:continue
            bars=s['bars'];n=len(bars)
            if len(s['prior14'])<14 or (policy=='ORB5_ACTIVITY_RUNNER_V2' and symbol not in activity_selection(prepared,day)):
                diagnostics['warmup_or_selection_exclusions']+=1;continue
            diagnostics['eligible_symbol_sessions']+=1
            count=0;j=5 if policy=='ORB5_ACTIVITY_RUNNER_V2' else 29
            opening_high=max(b.high for b in bars[:5]);opening_low=min(b.low for b in bars[:5])
            if policy=='ORB5_ACTIVITY_RUNNER_V2' and bars[4].close<=bars[0].open:continue
            while j<n-1 and count<POLICIES[policy]['max_entries_per_symbol_session']:
                if policy=='ORB5_ACTIVITY_RUNNER_V2':
                    if bars[j].high<opening_high:j+=1;continue
                    entry_index=j;raw_entry=max(bars[j].open,opening_high)
                    # This resting stop order was known at opening-range close.
                    at=bars[4].closed;stop=opening_low*D('.9995')
                else:
                    if not noise_condition(s,j):j+=1;continue
                    at=bars[j].closed;stop=s['lows'][j]*D('.9995')
                    entry_index=j+1;raw_entry=bars[entry_index].open
                    if raw_entry<=stop:j=entry_index;continue
                entry=raw_entry*buy
                if not 0<stop<entry: j=entry_index+1;continue
                risk=entry-stop;count+=1;clock_exit=False
                for k in range(entry_index,n):
                    b=bars[k];raw=None;reason=None;exit_at=b.closed
                    if b.open<=stop:
                        raw=b.open;reason='GAP_STOP';exit_at=b.opened
                    elif clock_exit:
                        raw=b.open;reason='CLOCK_EXIT';exit_at=b.opened
                    elif b.low<=stop:
                        raw=stop;reason='STOP_FIRST'
                    elif k==n-1:
                        raw=b.close;reason='SESSION_END'
                    if raw is not None:
                        exit_price=raw*sell;net=exit_price-entry-2*fee
                        result.append({'policy_id':policy,'symbol':symbol,'signal_at':at.isoformat(),
                            'entry_at':bars[entry_index].opened.isoformat(),'exit_at':exit_at.isoformat(),
                            'entry':str(entry),'exit':str(exit_price),'stop':str(stop),
                            'net_pnl_per_share':str(net),'net_r':str(net/risk),'reason':reason})
                        j=k+3;break
                    if policy=='NOISE_BAND_CLOCK_V2' and (k+1)%30==0:
                        clock_exit=not noise_condition(s,k)
                else:raise ValueError('V2 unresolved position')
    return result,diagnostics
