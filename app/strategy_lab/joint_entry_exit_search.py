"""Private-data offline joint search. No runtime, broker or provider entry point."""
import argparse
from itertools import product
import json
from pathlib import Path
from hashlib import sha256
import zipfile
import numpy as np
import pandas as pd
from app.strategy_lab.broad_strategy_lab import Case,indicators,rules
from app.strategy_lab.entry_family_comparison import GRID
from app.strategy_lab.daily_income_lab import engine,metrics


def pack(frames,dates,case,source_sell):
    symbols=sorted(frames);ohlcv=np.full((len(dates),len(symbols),5),np.nan)
    atr=np.full((len(dates),len(symbols)),np.nan);buy=np.zeros(atr.shape,dtype=np.bool_);sell=buy.copy()
    for j,s in enumerate(symbols):
        f=frames[s].copy();signal=rules(f,case);f['buy']=signal['buy'];f['sell']=signal['sell']
        f=f.set_index('date').reindex(dates)
        ohlcv[:,j]=f[['open','high','low','close','volume']].to_numpy();atr[:,j]=f.atr.to_numpy()
        buy[:,j]=f['buy'].fillna(False).to_numpy()
        if source_sell:sell[:,j]=f['sell'].fillna(False).to_numpy()
    ids=((dates-dates[0]).total_seconds()//86400).astype(np.int64).to_numpy()
    return ohlcv,atr,buy,sell,ids,int(ids[-1]+1)


def passes(row):
    return (row['closed_trades']>=20 and (row['win_rate'] or 0)>.6 and
        row['terminal_net_after_overhead']>0 and row['entry_active_days']==row['calendar_days'] and
        row['terminal_forced_closes']<=max(1,row['closed_trades']*.2) and row['maximum_drawdown']<=.15)


def run(archive,output,*,market_wide=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    cases=[Case(f,p) for f,ps in GRID.items() for p in ps]
    geometry=list(product((2.,3.,5.,8.),(2.,3.,5.,8.),(12,24,48,96),(False,True)))
    protocol=dict(input_sha256=sha256(Path(archive).read_bytes()).hexdigest(),entry_grid=GRID,
        stop_atr=[2,3,5,8],target_atr=[2,3,5,8],hold_hours=[12,24,48,96],source_exit_signal=[False,True],
        capital=10000,slots=6,fees_per_side=[.004,.008],slippage_per_side=.0005,overhead_per_day=2,
        prior_completed_candle_volume_participation_limit=.01,min_notional=10,
        gate='>60% closed wins, positive net after costs, entries every complete day, >=20 closes, drawdown <=15%',
        qualification_eligible=False,orders=0,workers_modified=False,market_wide=market_wide,
        limitations=['Exploratory, previously inspected latest-revision candle capture',
            'Later chronological split is not a pristine untouched holdout',
            'Screening engine; native replay required before any deployment'])
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2))
    with zipfile.ZipFile(archive) as z:
      for tf,prefix in ([('1h','bb'),('4h','nfi')] if market_wide else [('1h','roc'),('4h','wave')]):
        frames={}
        for name in z.namelist():
            if name.startswith(prefix+'/candles/') and name.endswith('-'+tf+'.json'):
                f=indicators(pd.DataFrame(json.loads(z.read(name))));f['eligible']=f.index>=200
                if not f.date.diff().dropna().eq(pd.Timedelta(tf)).all():raise ValueError('CONTIGUOUS_INPUT_REQUIRED')
                frames[Path(name).name.split('-')[0]]=f
        start=max(f.date.iloc[200] for f in frames.values()).ceil('D')
        end=min(f.date.iloc[-1]+pd.Timedelta(tf) for f in frames.values()).floor('D')
        total=(end-start).days;split=start+pd.Timedelta(days=int(total*.7))
        early=pd.date_range(start,split,freq=tf,inclusive='left');late=pd.date_range(split,end,freq=tf,inclusive='left')
        rows=[];passing=[];best=[]
        for case in cases:
          prepared={s:pack(frames,early,case,s) for s in (False,True)}
          for stop,target,hold,sell in geometry:
            identity=dict(case=case.identity,stop_atr=stop,target_atr=target,hold_hours=hold,source_sell=sell)
            scores=[]
            for fee in protocol['fees_per_side']:
                result=engine(*prepared[sell],10000.,fee,.0005,6,stop,target,max(1,hold//int(tf[:-1])))
                scores.append(dict(fee=fee,**metrics(result,10000.)))
            row=dict(**identity,scores=scores);rows.append(row)
            if all(passes(s) for s in scores):passing.append(row)
          print('JOINT_SEARCH='+tf+'; ENTRY='+case.identity+'; CASES='+str(len(rows)),flush=True)
        frozen=sorted(passing,key=lambda r:min(s['average_net_per_day'] for s in r['scores']),reverse=True)[:5]
        (output/(tf+'-selection.json')).write_text(json.dumps(dict(selected=frozen,passing=len(passing),
            early=[str(start),str(split)],later=[str(split),str(end)]),indent=2))
        checked=[]
        for row in frozen:
            case=next(c for c in cases if c.identity==row['case']);pre=pack(frames,late,case,row['source_sell'])
            scores=[]
            for fee in protocol['fees_per_side']:
                result=engine(*pre,10000.,fee,.0005,6,row['stop_atr'],row['target_atr'],max(1,row['hold_hours']//int(tf[:-1])))
                scores.append(dict(fee=fee,**metrics(result,10000.)))
            checked.append(dict(candidate=row,later_scores=scores,later_pass=all(passes(s) for s in scores)))
        (output/(tf+'-early.json')).write_text(json.dumps(rows,separators=(',',':')))
        (output/(tf+'-later.json')).write_text(json.dumps(checked,indent=2))
        print('JOINT_FINISHED='+tf+'; PASSING='+str(len(passing)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',required=True);p.add_argument('--output',required=True)
    p.add_argument('--market-wide',action='store_true')
    a=p.parse_args();run(a.archive,a.output,market_wide=a.market_wide)
