"""Frozen long-only recovery experiments; offline screening, never deployment."""
import argparse
from itertools import product
from hashlib import sha256,sha1
import json
from pathlib import Path
from urllib.request import urlopen
from concurrent.futures import ThreadPoolExecutor
from app.strategy_lab.broad_strategy_lab import Case,indicators,rules,replay,compact,prepare_replay
from app.strategy_lab.intraday_activity_lab import daily_metrics

SYMBOLS=('BTC','ETH','SOL','XRP','DOGE','LINK')
FAMILIES={'RSI_RECOVERY':[25,35,45],'BB_REENTRY':[1.5,2.,2.5],
          'DRAWDOWN_RECOVERY':[.03,.06,.10],'RANGE_RECLAIM':[40,45,50]}
CONTROLS=[Case('ROC3_PULLBACK',.08),Case('RSI2_TREND',10),Case('IBS_RANGE',.2)]


def grid():
    return [Case(f,dict(trigger=p,stop=stop,target=target,hours=hours))
            for f,values in FAMILIES.items() for p,stop,target,hours in product(values,(2.5,4.),(2.,4.),(24,72))]


def write(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def prepare(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    protocol=dict(scope='FROZEN_REGIME_RECOVERY_SCREEN_V1',symbols=list(SYMBOLS),timeframes=['1h','4h'],
       cases=[c.identity for c in grid()],controls=[c.identity for c in CONTROLS],
       segments={'development':['2023-10-01','2024-01-01'],'validation':['2024-04-01','2024-07-01'],
                 'holdout':['2024-07-01','2024-10-01']},
       fees_per_side=[.004,.008],slippage_per_side=.0005,signal_exit_net_floor=.005,
       initial_cash=10000,slots=6,minimum_order_notional_assumption=10,
       minimum_expected_target_surplus=.005,overhead_per_day_assumption=2,
       gate=dict(minimum_closed=60,win_rate=.6,profit_factor=1.15,maximum_drawdown=.15,
                 minimum_entries_per_day=1.,minimum_active_day_fraction=.6),
       selection_fee=.004,prior_research_seen=True,promotion='NONE',orders=0,
       limitations=['Exploratory research; prior broad 2024 studies inform this new grid.',
        'Binance USDT history does not qualify current Kraken USD execution.',
        'Fees/slippage are explicit assumptions, not verified account costs.',
        'Signal profit floor uses signal-close estimate; next-open gaps can exit at a loss.',
        'Stops take precedence over targets when both touched in one bar.',
        'No intrabar path, order-book fills, exchange precision or minimum size rules reproduced.'])
    protocol['implementation_sha256']={name:sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ('regime_recovery_lab.py','broad_strategy_lab.py')}
    write(root/'protocol.json',protocol)
    write(root/'protocol-lock.json',{'sha256':sha256((root/'protocol.json').read_bytes()).hexdigest()})
    return protocol


def protocol(root):
    root=Path(root);raw=(root/'protocol.json').read_bytes()
    if sha256(raw).hexdigest()!=json.loads((root/'protocol-lock.json').read_text())['sha256']:
        raise ValueError('FROZEN_PROTOCOL_CHANGED')
    result=json.loads(raw)
    if any(sha256((Path(__file__).parent/name).read_bytes()).hexdigest()!=expected for name,expected in result['implementation_sha256'].items()):raise ValueError('FROZEN_IMPLEMENTATION_CHANGED')
    return result


def aggregate_hourly(frame):
    f=frame.copy().set_index('date');groups=f.resample('4h',origin='epoch',closed='left',label='left')
    result=groups.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    return result.loc[groups['close'].count()==4].reset_index()


def fetch(root):
    import pandas as pd
    root=Path(root);p=protocol(root);folder=root/'data';folder.mkdir(exist_ok=False)
    lock=json.loads((Path(__file__).parent/'nfi-history-source-lock.json').read_text())
    selected=[i for i in lock['files'] if i['path'] in ['binance/'+s+'_USDT-1h.feather' for s in p['symbols']]]
    if len(selected)!=len(p['symbols']):raise ValueError('ALL_PINNED_SYMBOLS_REQUIRED')
    def one(item):
        name=Path(item['path']).name;url='https://raw.githubusercontent.com/DigiTuccar/HistoricalDataForTradeBacktest/'+lock['commit']+'/'+item['path']
        with urlopen(url,timeout=90) as response:raw=response.read(item['bytes']+1)
        if len(raw)!=item['bytes'] or sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=item['git_blob_sha']:
            raise ValueError('PINNED_HISTORY_MISMATCH')
        path=folder/name;path.write_bytes(raw);frame=pd.read_feather(path);frame['date']=pd.to_datetime(frame['date'],utc=True)
        frame=frame.sort_values('date').reset_index(drop=True)
        if frame.date.duplicated().any() or not (frame.date==frame.date.dt.floor('h')).all():raise ValueError('UNIQUE_ALIGNED_HOURLY_DATA_REQUIRED')
        derived=aggregate_hourly(frame);derived.to_feather(folder/name.replace('-1h','-4h'))
        print('PINNED_HISTORY',name,len(frame),'FIRST',frame.date.min(),'LAST',frame.date.max(),flush=True)
        return {**item,'sha256':sha256(raw).hexdigest(),'derived_4h_sha256':sha256((folder/name.replace('-1h','-4h')).read_bytes()).hexdigest()}
    with ThreadPoolExecutor(max_workers=3) as pool:manifest=list(pool.map(one,selected))
    write(root/'history-manifest.json',{'commit':lock['commit'],'files':manifest,'derived_4h':'Complete aligned groups of four hourly candles only'})


def features(frame):
    f=indicators(frame);f['eligible']=f.index>=200
    f['priorhigh24']=f['high'].rolling(24).max().shift()
    return f


def recovery_rules(f,case,tf,fee,slip):
    if case.family not in FAMILIES:return rules(f,case)
    p=case.parameter;trigger=p['trigger'];c=f['close'];rsi=f['rsi14'];lower=f['sma20']-trigger*f['std20']
    if case.family=='RSI_RECOVERY':
        buy=(rsi>=trigger)&(rsi.shift()<trigger)&(c>c.shift());sell=rsi>=60
    elif case.family=='BB_REENTRY':
        buy=(c.shift()<lower.shift())&(c>=lower)&(c>c.shift());sell=c>=f['sma20']
    elif case.family=='DRAWDOWN_RECOVERY':
        buy=(c<f['priorhigh24']*(1-trigger))&(c>f['high'].shift())&(rsi>rsi.shift());sell=c>=f['sma20']
    elif case.family=='RANGE_RECLAIM':
        swept=f['low'].shift()<f['priorlow20'].shift()
        buy=swept&(c>f['ema10'])&(rsi>=trigger);sell=c>=f['priorhigh20']
    # Frozen planned target must cover the modeled round-trip cost and surplus.
    expected_target=p['target']*f['atr']/c
    buy=buy&(expected_target>=2*fee+2*slip+.005)&(f['volume']>0)&f['eligible']
    return dict(buy=buy.fillna(False),sell=sell.fillna(False),stop_atr=p['stop'],target_atr=p['target'],
                trail_atr=None,timeout=max(1,p['hours']//(1 if tf=='1h' else 4)))


def gate(row,p):
    g=p['gate'];a=row['activity']
    return (row['closed_trades']>=g['minimum_closed'] and (row['win_rate'] or 0)>=g['win_rate']
        and (row['profit_factor'] or 0)>=g['profit_factor'] and row['maximum_drawdown']<=g['maximum_drawdown']
        and a['entries_per_day']>=g['minimum_entries_per_day'] and a['entry_active_day_fraction']>=g['minimum_active_day_fraction']
        and row['terminal_liquidation_net_after_overhead']>0)


def simulate(frames,case,calendar,tf,fee,p,bars):
    cfg={s:recovery_rules(f,case,tf,fee,p['slippage_per_side']) for s,f in frames.items()}
    signals={s:{at:(bool(b),bool(x),float(a)) for at,b,x,a in zip(f.date,cfg[s]['buy'],cfg[s]['sell'],f.atr)} for s,f in frames.items()}
    r=replay(frames,case,calendar,fee=fee,slip=p['slippage_per_side'],prepared=(bars,cfg,signals),
             initial=p['initial_cash'],slots=p['slots'],min_notional=p['minimum_order_notional_assumption'],
             signal_exit_floor=p['signal_exit_net_floor'] if case.family in FAMILIES else None)
    a=daily_metrics(r,calendar,p['overhead_per_day_assumption'])
    open_mark=r['ending_marked_equity']-r['ending_cash']
    liquidation=r['marked_net']-open_mark*(fee+p['slippage_per_side']-fee*p['slippage_per_side'])
    result={**compact(r),'activity':{k:v for k,v in a.items() if k!='daily'},
            'terminal_liquidation_net':liquidation,'terminal_liquidation_net_after_overhead':liquidation-a['calendar_days']*p['overhead_per_day_assumption']}
    result['passes_gate']=gate(result,p)
    return result,r


def run(root,tf):
    import pandas as pd
    root=Path(root);p=protocol(root);out=root/('screen-'+tf+'.json')
    if out.exists():raise ValueError('FRESH_RESULT_REQUIRED')
    frames={}
    manifest=json.loads((root/'history-manifest.json').read_text())
    earliest=pd.Timestamp(min(v[0] for v in p['segments'].values()),tz='UTC')-pd.Timedelta(days=60)
    latest=pd.Timestamp(max(v[1] for v in p['segments'].values()),tz='UTC')
    for symbol in p['symbols']:
        path=root/'data'/(symbol+'_USDT-'+tf+'.feather')
        item=next(i for i in manifest['files'] if Path(i['path']).name==symbol+'_USDT-1h.feather')
        if sha256(path.read_bytes()).hexdigest()!=item['sha256' if tf=='1h' else 'derived_4h_sha256']:raise ValueError('PINNED_DATA_CHANGED')
        f=pd.read_feather(path);f['date']=pd.to_datetime(f['date'],utc=True)
        frames[symbol]=features(f[(f.date>=earliest)&(f.date<latest)])
    dates=frames['BTC'].date
    calendars={n:list(dates[(dates>=pd.Timestamp(v[0],tz='UTC'))&(dates<pd.Timestamp(v[1],tz='UTC'))]) for n,v in p['segments'].items()}
    if any(not c for c in calendars.values()):raise ValueError('ALL_PERIODS_REQUIRE_HISTORY')
    coverage={}
    for name,calendar in calendars.items():
        expected=int((pd.Timestamp(p['segments'][name][1])-pd.Timestamp(p['segments'][name][0])).total_seconds()/(3600 if tf=='1h' else 14400))
        coverage[name]=dict(observed_bars=len(calendar),expected_bars=expected,missing_bars=expected-len(calendar))
        if len(calendar)<expected*.99:raise ValueError('BENCHMARK_CALENDAR_COVERAGE_BELOW_99_PERCENT')
    write(root/('coverage-'+tf+'.json'),coverage)
    bars=prepare_replay(frames,CONTROLS[0])[0];rows=[]
    for case in grid()+CONTROLS:
        row={'case':case.identity,'control':case.family not in FAMILIES}
        for stage in ('development','validation'):
            row[stage]={}
            for fee in p['fees_per_side']:
                result,_=simulate(frames,case,calendars[stage],tf,fee,p,bars)
                row[stage][str(fee)]=result
        rows.append(row)
        write(out,{'timeframe':tf,'completed_cases':len(rows),'planned_cases':len(grid())+len(CONTROLS),'cases':rows,'promotion':'NONE','orders':0})
        print('SCREEN',tf,len(rows),case.identity,flush=True)
    passing=[r for r in rows if not r['control'] and all(r[stage][str(p['selection_fee'])]['passes_gate'] for stage in ('development','validation'))]
    passing.sort(key=lambda r:(-r['validation'][str(p['selection_fee'])]['terminal_liquidation_net_after_overhead'],r['case']))
    selection={'selected':passing[0]['case'] if passing else None,'passing_candidates':len(passing),'fee':p['selection_fee'],'holdout_opened':False}
    with (root/('selection-'+tf+'.json')).open('x') as f:json.dump(selection,f,indent=2)
    if passing:
        family,params=selection['selected'].split(':',1);case=Case(family,json.loads(params));holdouts={}
        for fee in p['fees_per_side']:
            result,ledger=simulate(frames,case,calendars['holdout'],tf,fee,p,bars);holdouts[str(fee)]=result
            write(root/('holdout-ledger-'+tf+'-'+str(fee)+'.json'),ledger)
        write(root/('holdout-'+tf+'.json'),holdouts)
    return selection


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--prepare',action='store_true');p.add_argument('--fetch',action='store_true');p.add_argument('--timeframe',choices=['1h','4h'])
    a=p.parse_args()
    if a.prepare:prepare(a.root)
    elif a.fetch:fetch(a.root)
    else:print(json.dumps(run(a.root,a.timeframe),indent=2))

if __name__=='__main__':main()
