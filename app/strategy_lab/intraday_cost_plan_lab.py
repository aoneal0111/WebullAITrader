"""Exploratory cost-aware intraday plans; fixed targets, no live order imports."""
import json
from pathlib import Path
from app.strategy_lab.intraday_activity_lab import features,daily_metrics,gate
from app.strategy_lab.broad_strategy_lab import Case,replay,compact,prepare_replay


def plan(f,family,target):
    c=f['close'];trend=(f['ema50']>f['ema200'])&(c>f['ema200'])
    if family=='BREAKOUT_PLAN':buy=trend&(c>f['priorhigh20'])&(f['volume']>f['volmean'])
    elif family=='DIP_PLAN':buy=trend&(c<f['sma20']-2*f['std20'])&(c>f['open'])
    else:raise ValueError('FIXED_PLAN_REQUIRED')
    return dict(buy=buy.fillna(False)&f['eligible']&(f['volume']>0),sell=c.isna(),
        stop_atr=target/2,target_atr=target,trail_atr=None,timeout=96)


def run(root):
    import pandas as pd
    root=Path(root);p=json.loads((root/'protocol.json').read_text());frames={s:features(pd.DataFrame(json.loads((root/'data'/f'{s}-15m.json').read_text()))) for s in p['symbols']}
    dates=frames['BTC']['date'];segments={n:list(dates[(dates>=pd.Timestamp(v[0]))&(dates<pd.Timestamp(v[1]))]) for n,v in p['segments'].items()}
    bars=prepare_replay(frames,Case('IBS_RANGE',.2))[0]
    def prepared(family,target):
        cfg={s:plan(f,family,target) for s,f in frames.items()}
        # Generic replay's risk-unit slot carries the completed close here,
        # explicitly giving fill-anchored percentage distances, not ATR stops.
        sig={s:{at:(bool(b),False,float(unit)) for at,b,unit in zip(f['date'],cfg[s]['buy'],f['close'])} for s,f in frames.items()}
        return bars,cfg,sig
    out={'scope':'EXPLORATORY_COST_AWARE_15MIN_PLAN_V1','cases':[],'promotion':'NONE','orders':0}
    for family in ['BREAKOUT_PLAN','DIP_PLAN']:
        for target in [.02,.03,.04]:
            case=Case(family,target);row={'case':case.identity,'development':{},'validation':{}};data=prepared(family,target)
            for name in ['development','validation']:
                for fee in p['fees_per_side']:
                    r=replay(frames,case,segments[name],fee=fee,slip=.0005,prepared=data,min_notional=10)
                    r['activity']=daily_metrics(r,segments[name]);r['passes_activity_profit_gate']=gate(r)
                    r=compact(r);r['activity'].pop('daily');row[name][str(fee)]=r
            out['cases'].append(row)
    choices={};holdout={}
    for fee in p['fees_per_side']:
        rows=[r for r in out['cases'] if r['development'][str(fee)]['passes_activity_profit_gate'] and r['validation'][str(fee)]['passes_activity_profit_gate']]
        rows.sort(key=lambda r:(-r['validation'][str(fee)]['activity']['net_after_overhead'],r['case']))
        choices[str(fee)]=rows[0]['case'] if rows else None
    with (root/'selection.json').open('x') as f:json.dump(choices,f,indent=2)
    for fee,identity in choices.items():
        if identity is None:continue
        family,param=identity.split(':');case=Case(family,float(param));r=replay(frames,case,segments['holdout'],fee=float(fee),slip=.0005,prepared=prepared(family,float(param)),min_notional=10)
        r['activity']=daily_metrics(r,segments['holdout']);r['passes_activity_profit_gate']=gate(r)
        (root/f'holdout-{fee}.json').write_text(json.dumps(r));r=compact(r);r['activity'].pop('daily');holdout[fee]=r
    out.update(selection=choices,holdout=holdout);(root/'comparison.json').write_text(json.dumps(out,indent=2));print('COST_AWARE_SELECTED',choices,flush=True);return out


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);run(p.parse_args().root)
