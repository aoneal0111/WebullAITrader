"""Offline entry comparisons with fixed or family exits; no worker integration."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import zipfile
from app.strategy_lab.broad_strategy_lab import Case,indicators,rules,prepare_replay,replay,compact

GRID={'RSI2_TREND':[5,10,20],'RSI2_FAST':[5,10,20],
      'IBS_TREND':[.1,.2,.3],'IBS_RANGE':[.1,.2,.3],
      'BB_REVERSION':[1.5,2,2.5],'ROC3_PULLBACK':[.08,.04,.02],
      'DONCHIAN':[20,55,100],'TREND_PULLBACK':[10,20,50],
      'ATR_BREAKOUT':[0,.25,.5],'SQUEEZE_BREAKOUT':[10,20,55],
      'ROC_MOMENTUM':[.01,.02,.04],'EMA_CROSS':[(5,20),(10,50),(20,100)]}


def prepared(frames,case):
    bars,base,signals=prepare_replay(frames,Case('ROC3_PULLBACK',.08))
    for symbol,frame in frames.items():
        buy=rules(frame,case)['buy'];base[symbol]['buy']=buy
        signals[symbol]={at:(bool(b),old[1],old[2]) for at,b in zip(frame.date,buy)
                         for old in [signals[symbol][at]]}
    return bars,base,signals


def run(archive,output,*,source_exits=False):
    import pandas as pd
    archive=Path(archive);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    if archive.stat().st_size>4*1024**2:raise ValueError('BOUNDED_CAPTURE_REQUIRED')
    cases=[Case(f,p) for f,values in GRID.items() for p in values]
    protocol=dict(scope='OFFLINE_ENTRY_FAMILY_COMPARISON_V1',grid=GRID,
        input_sha256=sha256(archive.read_bytes()).hexdigest(),split=.7,warmup_bars=200,
        fees_per_side=[.004,.008],slippage_per_side=.0005,capital=10000,slots=6,
        selection_gate=dict(minimum_closed=20,minimum_win_rate=.6,positive_marked_net_both_fees=True),
        exits='Existing family research exit rules' if source_exits else dict(stop_atr=3,target=None,trail=None,timeout_bars=10,sell='close > SMA5'),
        workers_modified=False,orders=0,
        limitations=['Previously inspected capture; later split is not a pristine holdout',
            'Latest recorded native candle revisions; no original raw-candle normalization audit',
            'Research adaptations across timeframes, not exact upstream strategy reproductions',
            'Screening fills, no execution authorization; small universes and periods'])
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2))
    results={}
    with zipfile.ZipFile(archive) as z:
        if len(z.namelist())>128 or sum(i.file_size for i in z.infolist())>64*1024**2:raise ValueError('ARCHIVE_BOUND')
        for tf,prefix in [('5m','e0'),('1h','roc'),('4h','wave')]:
            frames={}
            for name in z.namelist():
                if name.startswith(prefix+'/candles/') and name.endswith('-'+tf+'.json'):
                    f=pd.DataFrame(json.loads(z.read(name)));f=indicators(f)
                    delta=pd.Timedelta(tf.replace('m','min'))
                    if not f.date.diff().dropna().eq(delta).all():raise ValueError('CONTIGUOUS_CANDLES_REQUIRED')
                    f['eligible']=f.index>=200;frames[Path(name).name.split('-')[0]]=f
            calendar=sorted(set.intersection(*(set(f.date.iloc[200:]) for f in frames.values())))
            cut=int(len(calendar)*.7);early=calendar[:cut];late=calendar[cut:];rows=[]
            for case in cases:
                pre=prepare_replay(frames,case) if source_exits else prepared(frames,case)
                for fee in protocol['fees_per_side']:
                    score=compact(replay(frames,case,early,fee=fee,slip=.0005,prepared=pre))
                    rows.append(dict(fee=fee,**score))
            passing=[]
            for case in cases:
                tests=[r for r in rows if r['case']==case.identity]
                if all(r['closed_trades']>=20 and (r['win_rate'] or 0)>=.6 and r['marked_net']>0 for r in tests):passing.append(case)
            selected=max(passing,key=lambda c:min(r['marked_net'] for r in rows if r['case']==c.identity)) if passing else None
            freeze=dict(selected=selected.identity if selected else None,passing=[c.identity for c in passing])
            exploratory=[]
            if source_exits and selected is None:
                for case in cases:
                    tests=[r for r in rows if r['case']==case.identity]
                    if all(r['marked_net']>0 and (r['win_rate'] or 0)>=.6 for r in tests):exploratory.append(case)
                exploratory=sorted(exploratory,key=lambda c:min(r['marked_net'] for r in rows if r['case']==c.identity),reverse=True)[:2]
            freeze['exploratory_only']=[c.identity for c in exploratory]
            (output/(tf+'-selection.json')).write_text(json.dumps(freeze,indent=2))
            later=[]
            for case in ([selected] if selected else exploratory)+[Case('ROC3_PULLBACK',.08)]:
                for fee in protocol['fees_per_side']:
                    pre=prepare_replay(frames,case) if source_exits else prepared(frames,case)
                    later.append(dict(fee=fee,**compact(replay(frames,case,late,fee=fee,slip=.0005,prepared=pre))))
            results[tf]=dict(symbols=sorted(frames),early=[str(early[0]),str(early[-1])],
                later=[str(late[0]),str(late[-1])],selection=freeze,early_cases=rows,later_cases=later)
            print('ENTRY_FAMILY_DONE='+tf+'; SELECTED='+str(freeze['selected']),flush=True)
    (output/'results.json').write_text(json.dumps(results,indent=2,allow_nan=False))
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',required=True);p.add_argument('--output',required=True)
    p.add_argument('--source-exits',action='store_true')
    a=p.parse_args();run(a.archive,a.output,source_exits=a.source_exits)
