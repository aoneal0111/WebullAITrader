"""Daily opportunity frequency for fixed source candidates on a broader universe."""
import json
from pathlib import Path
from app.strategy_research.source_candidate_lab import replay,CANDIDATES
from app.strategy_lab.intraday_activity_lab import daily_metrics,gate
from app.strategy_lab.broad_strategy_lab import compact,indicators,Case,replay as broad_replay


def session_metrics(r,calendar):
    a=daily_metrics(r,calendar);allowed={str(x)[:10] for x in calendar};days=[x for x in a['daily'] if x['day'] in allowed];n=len(days)
    return dict(trading_sessions=n,entries_per_day=r['entries']/n,entry_active_day_fraction=sum(x['entries']>0 for x in days)/n,
        entry_active_days=sum(x['entries']>0 for x in days),positive_day_fraction=sum(x['marked_net']>0 for x in days)/n,
        net_per_trading_session=r['marked_net']/n,net_after_overhead=r['marked_net']-2*n,overhead_per_session_assumption=2.,
        worst_marked_day=min(x['marked_net'] for x in days),daily=days)


def evaluate(rows,case,start,end,fee):
    data={s:{d:b for d,b in f.items() if d<end} for s,f in rows.items()}
    calendar=[d for d in sorted(data['SPY']) if d>=start]
    if case in CANDIDATES:
        r=replay(data,case,start,fee_bps=fee,slippage_bps=5,include_curve=True)
    else:
        import pandas as pd
        frames={s:indicators(pd.DataFrame([dict(date=d,**b) for d,b in values.items()])) for s,values in data.items()}
        for frame in frames.values():frame['eligible']=frame.index>=200
        calendar=list(pd.to_datetime(calendar,utc=True))
        r=broad_replay(frames,Case('RSI2_FAST',20),calendar,fee=fee/10000,slip=.0005,equity_shares=True)
    r['activity']=session_metrics(r,calendar);r['passes_activity_profit_gate']=gate(r)
    r['open_positions_count']=len(r['open_positions']) if isinstance(r['open_positions'],dict) else r['open_positions']
    return r


def run(root):
    root=Path(root);p=json.loads((root/'protocol.json').read_text());rows={}
    for s in p['symbols']:
        f=root/'data'/(s+'.json')
        if f.exists():rows[s]={x['date'][:10]:{k:x[k] for k in ('open','high','low','close','volume')} for x in json.loads(f.read_text())}
    report={'scope':'BROADER_EQUITY_DAILY_ACTIVITY_RESEARCH_V1','universe':list(rows),'cases':[],'promotion':'NONE','orders':0}
    if len(rows)<25 or 'SPY' not in rows:raise ValueError('INSUFFICIENT_DECLARED_UNIVERSE_DATA')
    for case in p['cases']:
        item={'case':case,'development':{},'validation':{}}
        for name in ['development','validation']:
            start,end=p['segments'][name]
            for fee in p['fees_bps_per_side']:
                r=evaluate(rows,case,start,end,fee);r['open_positions']=r['open_positions_count']
                result=compact(r);result['activity']={k:v for k,v in r['activity'].items() if k!='daily'};item[name][str(fee)]=result
        report['cases'].append(item)
    selected={};holdout={}
    for fee in p['fees_bps_per_side']:
        passing=[r for r in report['cases'] if r['development'][str(fee)]['passes_activity_profit_gate'] and r['validation'][str(fee)]['passes_activity_profit_gate']]
        passing.sort(key=lambda r:(-r['validation'][str(fee)]['activity']['net_after_overhead'],r['case']))
        selected[str(fee)]=passing[0]['case'] if passing else None
    with (root/'selection.json').open('x') as f:json.dump(selected,f,indent=2)
    for fee,case in selected.items():
        if case is None:continue
        start,end=p['segments']['holdout']
        r=evaluate(rows,case,start,end,float(fee))
        (root/f'holdout-{fee}.json').write_text(json.dumps(r));item=compact(r);item['open_positions']=r['open_positions_count'];item['activity']={k:v for k,v in r['activity'].items() if k!='daily'};holdout[fee]=item
    report.update(selection=selected,holdout=holdout);(root/'comparison.json').write_text(json.dumps(report,indent=2));print('EQUITY_ACTIVITY',len(rows),'SYMBOLS','SELECTED',selected,flush=True);return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);run(p.parse_args().root)
