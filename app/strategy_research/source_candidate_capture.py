"""Bounded historical SIP daily capture for the offline source candidate lab."""
import argparse
import csv
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from .selected_sip_capture import request_page,credentials_from_local,CaptureError
from .source_candidate_lab import load_csv

SYMBOLS=('SPY','AAPL','MSFT','NVDA','AMZN','GOOGL','META','AVGO','AMD','ADBE','CRM',
 'COST','PEP','NKE','MCD','HON','CAT','DE','UNH','LLY','ABBV','XOM','CVX','LIN','TXN','QCOM','ORCL','INTU','NOW','PANW','MU')
START='2014-01-01';END='2026-11-01'


def capture(repo,output,*,fetch=request_page,credentials=None):
    output=Path(output);output.mkdir(exist_ok=False,parents=True)
    report=dict(scope='SOURCE_CANDIDATE_DAILY_CAPTURE_V1',status='PARTIAL',feed='sip',adjustment='all',
        start=START,end_exclusive=END,symbols=SYMBOLS,attempts=0,orders=0,grok_requests=0,pages=[])
    rows={};token=None;seen=set();total=0
    try:
        credentials=credentials if credentials is not None else credentials_from_local(repo)
        if not all(credentials):raise CaptureError('LOCAL_ALPACA_CREDENTIALS_MISSING')
        for page in range(40):
            params=dict(symbols=','.join(SYMBOLS),timeframe='1Day',start=START,end=END,
                        adjustment='all',feed='sip',asof='-',sort='asc',limit=10000)
            if token:params['page_token']=token
            report['attempts']+=1;raw=fetch(params,credentials);total+=len(raw)
            if len(raw)>4000000 or total>64*1024**2:raise CaptureError('DAILY_RESPONSE_BOUND')
            (output/f'page-{page:03d}.json').write_bytes(raw)
            report['pages'].append(dict(file=f'page-{page:03d}.json',sha256=sha256(raw).hexdigest()))
            body=json.loads(raw)
            for symbol,bars in body['bars'].items():
                if symbol not in SYMBOLS:raise CaptureError('UNREQUESTED_SYMBOL')
                for b in bars:
                    at=datetime.fromisoformat(b['t'].replace('Z','+00:00'))
                    if at.utcoffset() is None:raise CaptureError('NAIVE_PROVIDER_TIME')
                    day=at.date().isoformat()
                    if not START<=day<END:raise CaptureError('BAR_OUTSIDE_FROZEN_WINDOW')
                    key=(symbol,day)
                    if key in rows:raise CaptureError('DUPLICATE_DAILY_BAR')
                    rows[key]=dict(date=day,symbol=symbol,**{dst:b[src] for dst,src in
                        [('open','o'),('high','h'),('low','l'),('close','c'),('volume','v')]})
            token=body.get('next_page_token')
            print('DAILY_HISTORY_PAGES='+str(page+1),flush=True)
            if not token:break
            if token in seen:raise CaptureError('REPEATED_PAGE_TOKEN')
            seen.add(token)
        else:raise CaptureError('DAILY_PAGINATION_BOUND')
        if {s for s,d in rows}!=set(SYMBOLS):raise CaptureError('INCOMPLETE_DAILY_UNIVERSE')
        csvpath=output/'daily.csv'
        with csvpath.open('x',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=('date','symbol','open','high','low','close','volume'));w.writeheader()
            w.writerows(rows[k] for k in sorted(rows))
        validated=load_csv(csvpath)
        if any(len(b)<250 for b in validated.values()):raise CaptureError('DAILY_WARMUP_COVERAGE_MISSING')
        report.update(status='COMPLETE',rows=len(rows),csv_sha256=sha256(csvpath.read_bytes()).hexdigest(),
            limitations=['Selected surviving large caps, not a point-in-time unbiased universe',
                         'Daily source-rule adaptation; no intraday quote or live-performance claim'])
    except Exception as exc:
        report['error']=str(exc) if isinstance(exc,CaptureError) else type(exc).__name__
        raise
    finally:
        (output/'capture-summary.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();capture(a.repo,a.output)


if __name__=='__main__':main()
