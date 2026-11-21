"""Offline activity-first crypto research. Never changes a running strategy."""
import json,math
from pathlib import Path
from collections import Counter
from app.strategy_lab.broad_strategy_lab import Case,indicators,rules,replay,compact,prepare_replay

FAMILIES={'BB_ACTIVE':[1.5,2.,2.5],'RSI14_PULLBACK':[25,30,35],
          'VWAP_PULLBACK':[.002,.004,.006],'VOLUME_BREAKOUT':[1.2,1.5,2.],
          'RSI2_FAST':[20],'DONCHIAN':[20],'IBS_RANGE':[.2]}


def features(frame):
    f=indicators(frame);pv=f['close']*f['volume']
    f['rolling_vwap96']=pv.rolling(96).sum()/f['volume'].rolling(96).sum()
    f['eligible']=f.index>=200
    return f


def active_rules(f,case):
    n,p=case.family,case.parameter;c=f['close'];stop=2.;target=None;trail=None;timeout=24
    if n=='BB_ACTIVE':buy=(c<f['sma20']-p*f['std20'])&(f['rsi14']<45);sell=c>=f['sma20'];stop=3.;timeout=12
    elif n=='RSI14_PULLBACK':buy=(c>f['ema200'])&(f['rsi14']<p);sell=f['rsi14']>55;target=3.
    elif n=='VWAP_PULLBACK':buy=(f['ema50']>f['ema200'])&(c>f['ema200'])&(c<f['rolling_vwap96']*(1-p));sell=c>=f['rolling_vwap96'];target=3.
    elif n=='VOLUME_BREAKOUT':buy=(c>f['priorhigh10'])&(c>f['ema50'])&(f['volume']>p*f['volmean']);sell=c<f['ema10'];trail=2.
    else:return rules(f,case)
    return dict(buy=buy.fillna(False)&f['eligible']&(f['volume']>0),sell=sell.fillna(False),
        stop_atr=stop,target_atr=target,trail_atr=trail,timeout=timeout)


def daily_metrics(result,calendar,overhead_per_day=2.):
    import pandas as pd
    if not calendar:return {}
    dates=pd.date_range(pd.Timestamp(calendar[0]).normalize(),pd.Timestamp(calendar[-1]).normalize(),freq='D')
    end_equity={}
    for row in result['curve']:end_equity[pd.Timestamp(row['at']).normalize()]=row['equity']
    entry_counts=Counter(pd.Timestamp(e['at']).normalize() for e in result['events'] if e['action']=='BUY')
    prior=10000.;daily=[]
    for day in dates:
        value=end_equity.get(day,prior);net=value-prior;prior=value
        daily.append(dict(day=str(day.date()),entries=entry_counts[day],marked_net=net,after_overhead=net-overhead_per_day))
    return dict(calendar_days=len(daily),entries_per_day=result['entries']/len(daily),
        entry_active_days=sum(x['entries']>0 for x in daily),entry_active_day_fraction=sum(x['entries']>0 for x in daily)/len(daily),
        positive_marked_days=sum(x['marked_net']>0 for x in daily),positive_day_fraction=sum(x['marked_net']>0 for x in daily)/len(daily),
        net_per_calendar_day=result['marked_net']/len(daily),overhead_per_day_assumption=overhead_per_day,
        net_after_overhead=result['marked_net']-overhead_per_day*len(daily),
        worst_marked_day=min(x['marked_net'] for x in daily),daily=daily)


def gate(r):
    a=r['activity']
    return (r['closed_trades']>=60 and r['profit_factor'] is not None and r['profit_factor']>=1.15
        and r['maximum_drawdown']<=.15 and a['entries_per_day']>=1
        and a['entry_active_day_fraction']>=.6 and a['net_after_overhead']>0)


def run(root):
    import pandas as pd
    root=Path(root);protocol=json.loads((root/'protocol.json').read_text());out={'scope':'ACTIVITY_FIRST_CROSS_VENUE_RESEARCH_V1','timeframes':{},'promotion':'NONE','orders':0}
    for tf in ['5m','15m']:
        frames={s:features(pd.DataFrame(json.loads((root/'data'/f'{s}-{tf}.json').read_text()))) for s in protocol['symbols']}
        dates=frames['BTC']['date'];segments={n:list(dates[(dates>=pd.Timestamp(v[0]))&(dates<pd.Timestamp(v[1]))]) for n,v in protocol['segments'].items()}
        bars=prepare_replay(frames,Case('IBS_RANGE',.2))[0];cases=[]
        for family,parameters in FAMILIES.items():
            for parameter in parameters:
                case=Case(family,parameter);configs={s:active_rules(f,case) for s,f in frames.items()}
                signals={s:{at:(bool(b),bool(x),float(a)) for at,b,x,a in zip(f['date'],configs[s]['buy'],configs[s]['sell'],f['atr'])} for s,f in frames.items()}
                prepared=(bars,configs,signals);row={'case':case.identity,'development':{},'validation':{}}
                for segment in ['development','validation']:
                    for fee in protocol['fees_per_side']:
                        r=replay(frames,case,segments[segment],fee=fee,slip=.0005,prepared=prepared,min_notional=protocol['minimum_order_notional_assumption'])
                        r['activity']=daily_metrics(r,segments[segment]);row[segment][str(fee)]={**compact(r),'activity':{k:v for k,v in r['activity'].items() if k!='daily'}}
                        row[segment][str(fee)]['passes_activity_profit_gate']=gate(row[segment][str(fee)])
                cases.append(row)
        # A separate frozen choice for each fee scenario; low-fee choices do not
        # qualify the strategy for Kraken's entry-tier taker rate.
        choices={};holdouts={}
        for fee in protocol['fees_per_side']:
            passing=[r for r in cases if r['development'][str(fee)]['passes_activity_profit_gate'] and r['validation'][str(fee)]['passes_activity_profit_gate']]
            passing.sort(key=lambda r:(-r['validation'][str(fee)]['activity']['net_after_overhead'],r['case']))
            choices[str(fee)]=passing[0]['case'] if passing else None
        with (root/f'selection-{tf}.json').open('x') as f:json.dump(choices,f,indent=2)
        for fee,selected in choices.items():
            if selected is None:continue
            family,parameter=selected.split(':',1);case=Case(family,json.loads(parameter));configs={s:active_rules(f,case) for s,f in frames.items()}
            signals={s:{at:(bool(b),bool(x),float(a)) for at,b,x,a in zip(f['date'],configs[s]['buy'],configs[s]['sell'],f['atr'])} for s,f in frames.items()}
            r=replay(frames,case,segments['holdout'],fee=float(fee),slip=.0005,prepared=(bars,configs,signals),min_notional=protocol['minimum_order_notional_assumption']);r['activity']=daily_metrics(r,segments['holdout']);r['passes_activity_profit_gate']=gate(r)
            (root/f'holdout-{tf}-{fee}.json').write_text(json.dumps(r));holdouts[fee]={**compact(r),'activity':{k:v for k,v in r['activity'].items() if k!='daily'}}
        out['timeframes'][tf]=dict(cases=cases,selection=choices,holdout=holdouts)
        (root/'comparison.json').write_text(json.dumps(out,indent=2));print('ACTIVITY_STUDY',tf,'CASES',len(cases),'SELECTED',choices,flush=True)
    return out


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);run(p.parse_args().root)


if __name__=='__main__':main()
