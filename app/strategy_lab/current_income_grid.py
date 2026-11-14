"""Replay the predeclared grid on a bounded current Kraken USD snapshot.

Reuses verified hourly raw captures and requests four-hour bars once per symbol.
No credentials or execution. Current-window ranking is exploratory, not deployment.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import urlopen
import numpy as np
import pandas as pd
from app.strategy_lab.daily_income_lab import Spec,features,load_protocol,metrics,pack,simulate,write


def frame_from_raw(raw,server_time,hours):
    data=json.loads(raw)
    if data.get('error'):raise ValueError('PROVIDER_REJECTED_MARKET')
    keys=[k for k in data['result'] if k!='last']
    if len(keys)!=1:raise ValueError('ONE_MARKET_REQUIRED')
    f=pd.DataFrame(data['result'][keys[0]],columns=['timestamp','open','high','low','close','vwap','volume','trades'])
    f=f[f.timestamp.astype('int64')+hours*3600<=server_time].copy()
    f['date']=pd.to_datetime(f.timestamp.astype('int64'),unit='s',utc=True)
    for c in ('open','high','low','close','volume'):f[c]=f[c].astype(float)
    f=f[['date','open','high','low','close','volume']].reset_index(drop=True)
    if f.date.duplicated().any() or not f.date.is_monotonic_increasing:raise ValueError('UNIQUE_COMPLETED_BARS_REQUIRED')
    return f


def run(root,capture,protocol):
    root,capture=Path(root),Path(capture);p=load_protocol(protocol)
    root.mkdir(exist_ok=False,parents=True)
    prior=json.loads((capture/'capture.json').read_text())
    server_time=int(pd.Timestamp(prior['server_time']).timestamp())
    manifest=[];rawdir=root/'raw-4h';rawdir.mkdir()
    report=dict(scope='CURRENT_KRAKEN_FROZEN_INCOME_GRID_V1',protocol_sha256=sha256((Path(protocol)/'protocol.json').read_bytes()).hexdigest(),
        capture_server_time=prior['server_time'],source_grid_frozen_before_snapshot=True,
        implementation_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        new_public_requests=14,reused_hourly_capture_requests=15,orders=0,workers_modified=False,
        qualification_eligible=False,timeframes={},limitations=[
        'Short latest-720-candle windows; hourly and four-hour windows differ',
        'Absolute-oversold 1200-bar SMA cannot warm and is explicitly excluded',
        'Current Kraken USD candles, modeled fees and slippage; no execution replay',
        'All-grid current ranking is exploratory; does not create an unseen finalist holdout'])
    hourly={}
    for item in prior['pairs']:
        if item['status']!='COMPLETED_CANDLES':continue
        raw=(capture/'raw'/(item['symbol']+'.json')).read_bytes()
        if sha256(raw).hexdigest()!=item['raw_sha256']:raise ValueError('FROZEN_HOURLY_CAPTURE_CHANGED')
        hourly[item['symbol']]=frame_from_raw(raw,server_time,1)
    def fetch(symbol):
        pair={'BTC':'XBTUSD','DOGE':'XDGUSD'}.get(symbol,symbol+'USD')
        url='https://api.kraken.com/0/public/OHLC?pair='+pair+'&interval=240'
        try:
            with urlopen(url,timeout=30) as response:raw=response.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError('BOUNDED_PUBLIC_RESPONSE_REQUIRED')
            (rawdir/(symbol+'.json')).write_bytes(raw)
            f=frame_from_raw(raw,server_time,4)
            return symbol,f,dict(symbol=symbol,rows=len(f),start=str(f.date.min()),end=str(f.date.max()),raw_sha256=sha256(raw).hexdigest())
        except Exception as exc:return symbol,None,dict(symbol=symbol,error_type=type(exc).__name__)
    with ThreadPoolExecutor(max_workers=2) as pool:items=list(pool.map(fetch,sorted(hourly)))
    four_hour={s:f for s,f,_ in items if f is not None and len(f)>=400}
    write(root/'capture-4h.json',dict(rows=[r for _,_,r in items],anchor_server_time=prior['server_time']))
    specs=[Spec(**s) for s in p['specs'] if s['family']!='ABSOLUTE_OVERSOLD']
    for tf,frames in [('1h',hourly),('4h',four_hour)]:
        if len(frames)<8:raise ValueError('EIGHT_MARKETS_REQUIRED')
        hours=1 if tf=='1h' else 4
        first=max(f.date.min()+pd.Timedelta(hours=hours*200) for f in frames.values()).ceil('D')
        end=min(f.date.max()+pd.Timedelta(hours=hours) for f in frames.values()).floor('D')
        if (end-first).days<7:raise ValueError('SEVEN_COMPLETE_DAYS_REQUIRED')
        ready={}
        for symbol,f in frames.items():
            f=features(f)
            f['eligible']=f.date.diff().eq(pd.Timedelta(hours=hours)).rolling(200,min_periods=200).sum().eq(200)
            ready[symbol]=f
        setting=dict(current=[first.strftime('%Y-%m-%d'),end.strftime('%Y-%m-%d')])
        rows=[];key=None;packed=None
        for index,spec in enumerate(specs):
            if key!=(spec.family,spec.trigger):
                packed=pack(ready,setting,tf,'current',spec);key=(spec.family,spec.trigger)
            for fee in (.004,.008):
                for capital in (10000.,25000.,50000.,100000.):
                    result=simulate(packed,spec,tf,fee,capital,.0005,6)
                    rows.append(dict(name=spec.name,spec=asdict(spec),timeframe=tf,fee_per_side=fee,
                                **metrics(result,capital,2.,100.)))
            if (index+1)%45==0:
                print('CURRENT_GRID',tf,'CONFIGURATIONS',index+1,'RUNS',len(rows),flush=True)
        report['timeframes'][tf]=dict(period=setting['current'],days=(end-first).days,symbols=sorted(ready),
            configurations=len(specs),excluded_warmup_configurations=45,runs=len(rows),rows=rows)
        write(root/'review.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--capture',type=Path,required=True);p.add_argument('--protocol',type=Path,required=True)
    a=p.parse_args();run(a.root,a.capture,a.protocol)


if __name__=='__main__':main()
