"""One bounded public Kraken snapshot; frozen historical finalists only.

No credentials, orders or worker changes. A short latest-window check is not a
long-term holdout, source reproduction, or execution qualification.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC,datetime
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import numpy as np
import pandas as pd
from app.strategy_lab.daily_income_lab import Spec,features,metrics,pack,simulate,write


def fetch(root, selection):
    root=Path(root);root.mkdir(exist_ok=False,parents=True)
    source=Path(selection).read_bytes();selected=json.loads(source)['selected']
    write(root/'selection-copy.json',dict(selected=selected,sha256=sha256(source).hexdigest()))
    raw_dir=root/'raw';raw_dir.mkdir();requests=0
    def request(endpoint,params):
        url='https://api.kraken.com/0/public/'+endpoint+'?'+urlencode(params)
        with urlopen(url,timeout=30) as response:raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('BOUNDED_PUBLIC_RESPONSE_REQUIRED')
        return raw,json.loads(raw)
    raw,server=request('Time',{});requests+=1;(raw_dir/'server-time.json').write_bytes(raw)
    server_time=int(server['result']['unixtime'])
    # Refuse to silently evaluate a provider snapshot from a different date.
    if abs(server_time-int(datetime.now(UTC).timestamp()))>3600:
        raise ValueError('PROVIDER_AND_RESEARCH_CLOCK_DISAGREE')
    symbols=('ADA','ATOM','AVAX','BTC','DOGE','ETH','LINK','LTC','ONE','SHIB','SOL','VET','XLM','XRP')
    def one(symbol):
        pair={'BTC':'XBTUSD','DOGE':'XDGUSD'}.get(symbol,symbol+'USD')
        try:
            raw,data=request('OHLC',dict(pair=pair,interval=60))
            (raw_dir/(symbol+'.json')).write_bytes(raw)
            if data.get('error'):return symbol,None,dict(symbol=symbol,status='PROVIDER_REJECTED',errors=data['error'],raw_sha256=sha256(raw).hexdigest())
            result=data['result'];keys=[k for k in result if k!='last']
            if len(keys)!=1:raise ValueError('ONE_MARKET_REQUIRED')
            frame=pd.DataFrame(result[keys[0]],columns=['timestamp','open','high','low','close','vwap','volume','trades'])
            frame=frame[frame.timestamp.astype('int64')+3600<=server_time].copy()
            frame['date']=pd.to_datetime(frame.timestamp.astype('int64'),unit='s',utc=True)
            frame=frame[['date','open','high','low','close','volume']]
            for c in ('open','high','low','close','volume'):frame[c]=frame[c].astype(float)
            if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:raise ValueError('UNIQUE_COMPLETED_HISTORY_REQUIRED')
            frame.to_feather(root/(symbol+'-1h.feather'))
            return symbol,frame,dict(symbol=symbol,status='COMPLETED_CANDLES',rows=len(frame),
                start=str(frame.date.min()),end=str(frame.date.max()),raw_sha256=sha256(raw).hexdigest())
        except Exception as exc:
            return symbol,None,dict(symbol=symbol,status='CAPTURE_FAILED',error_type=type(exc).__name__)
    with ThreadPoolExecutor(max_workers=2) as pool:items=list(pool.map(one,symbols))
    requests+=len(symbols)
    frames={s:f for s,f,_ in items if f is not None and len(f)>=400}
    report=dict(scope='PUBLIC_KRAKEN_CURRENT_INCOME_SNAPSHOT_V1',public_requests=requests,
        server_time=datetime.fromtimestamp(server_time,UTC).isoformat(),pairs=[r for _,_,r in items],
        frozen_selection_sha256=sha256(source).hexdigest(),network_requests_during_replay=0,
        orders=0,workers_modified=False,qualification_eligible=False,
        limitations=['Latest hourly API window only; current unfinished candle discarded',
           '4h finalists and 1200-hour SMA finalists cannot be warmed from this snapshot',
           'No artificial warmup reduction or missing history reconstruction',
           'Different USD pair universe from historical USDT screen; short period only'])
    write(root/'capture.json',report)
    if len(frames)<8:raise ValueError('EIGHT_CURRENT_MARKETS_REQUIRED')
    first=max(f.date.min()+pd.Timedelta(hours=200) for f in frames.values()).ceil('D')
    end=min(f.date.max()+pd.Timedelta(hours=1) for f in frames.values()).floor('D')
    if (end-first).days<7:raise ValueError('SEVEN_COMPLETED_EVALUATION_DAYS_REQUIRED')
    ready={}
    for s,f in frames.items():
        f=features(f.reset_index(drop=True))
        f['eligible']=f.date.diff().eq(pd.Timedelta(hours=1)).rolling(200,min_periods=200).sum().eq(200)
        ready[s]=f
    p=dict(current=[first.strftime('%Y-%m-%d'),end.strftime('%Y-%m-%d')])
    report.update(evaluation=p['current'],calendar_days=(end-first).days,ready_symbols=sorted(ready),rows=[],skipped=[])
    for item in selected:
        spec=Spec(**item['spec']);tf=item['timeframe']
        if tf!='1h' or spec.family=='ABSOLUTE_OVERSOLD':
            report['skipped'].append(dict(timeframe=tf,name=spec.name,reason='HISTORY_WARMUP_UNAVAILABLE'))
            continue
        packed=pack(ready,p,'1h','current',spec)
        for fee in (.004,.008):
            for capital in (10000.,25000.,50000.,100000.):
                result=simulate(packed,spec,'1h',fee,capital,.0005,6)
                row=dict(name=spec.name,spec=asdict(spec),timeframe='1h',fee_per_side=fee,
                         **metrics(result,capital,2.,100.))
                report['rows'].append(row)
    write(root/'current-review.json',report)
    print('CURRENT_INCOME_REVIEW; RUNS='+str(len(report['rows']))+'; DAYS='+str(report['calendar_days'])+'; ORDERS=0',flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--selection',type=Path,required=True);args=parser.parse_args()
    fetch(args.root,args.selection)


if __name__=='__main__':main()
