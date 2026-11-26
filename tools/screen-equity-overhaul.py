"""Bounded public-minute screen; no credentials, accounts, GUI or order clients.

Yahoo minute history is a limited external research source, not consolidated
NBBO execution evidence. Missing sessions remain excluded, never manufactured.
"""
import argparse
from dataclasses import asdict
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sys,time,urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.market.calendar import NYSE
from app.strategy_research.candidates import Bar,validate_bars
from app.strategy_research.replay import Costs
from app.equity_overhaul.strategies import RULES
from app.equity_overhaul.replay import replay

SYMBOLS=('SPY','QQQ','AAPL','MSFT','NVDA','TSLA')
START=datetime(2026,9,25,tzinfo=UTC)
END=datetime(2026,10,23,tzinfo=UTC)


def capture(root):
    root.mkdir(parents=True,exist_ok=False)
    manifest=dict(source='Yahoo public chart, external OHLCV research only',execution_authorized=False,
                  captured_at=datetime.now(UTC).isoformat(),responses=[],coverage=[])
    schedule=NYSE.schedule(start_date=START.date(),end_date=(END-timedelta(days=1)).date())
    outputs=[]
    for symbol in SYMBOLS:
        rows={}
        for index in range(4):
            left=START+timedelta(days=index*7);right=min(left+timedelta(days=7),END)
            url=(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
                 f'?period1={int(left.timestamp())}&period2={int(right.timestamp())}&interval=1m&includePrePost=false')
            for attempt in range(3):
                try:
                    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 AtlasOfflineResearch'})
                    with urllib.request.urlopen(req,timeout=25) as response:raw=response.read(3000001)
                    if len(raw)>3000000:raise ValueError('response bound exceeded')
                    break
                except OSError:
                    if attempt==2:raise
                    time.sleep(1+attempt)
            name=f'{symbol}-{index}.json';(root/name).write_bytes(raw)
            manifest['responses'].append(dict(file=name,sha256=hashlib.sha256(raw).hexdigest(),url=url))
            payload=json.loads(raw)['chart']['result'][0]
            quote=payload['indicators']['quote'][0]
            for i,t in enumerate(payload.get('timestamp',[])):
                if not int(left.timestamp())<=t<int(right.timestamp()):continue
                values=tuple(quote[k][i] for k in ('open','high','low','close','volume'))
                if any(x is None for x in values):continue
                if t in rows and rows[t]!=values:raise ValueError('conflicting public minute')
                rows[t]=values
            time.sleep(.4)
        bars=[]
        for _,r in schedule.iterrows():
            opened,closed=r.market_open.to_pydatetime(),r.market_close.to_pydatetime()
            wanted=[int((opened+timedelta(minutes=i)).timestamp()) for i in range(int((closed-opened).total_seconds()/60))]
            missing=[t for t in wanted if t not in rows]
            manifest['coverage'].append(dict(symbol=symbol,date=opened.date().isoformat(),expected=len(wanted),
                missing=len(missing),status='EXCLUDED_INCOMPLETE' if missing else 'COMPLETE'))
            if missing:continue
            for t in wanted:
                op,hi,lo,cl,vol=rows[t];at=datetime.fromtimestamp(t,UTC)
                bars.append(Bar(symbol,at,at+timedelta(minutes=1),D(str(op)),D(str(hi)),D(str(lo)),D(str(cl)),D(str(vol)),closed))
        if not bars:continue
        validate_bars(tuple(bars),'minute')
        path=root/f'{symbol}-normalized.json'
        path.write_text(json.dumps([asdict(b) for b in bars],default=str))
        for policy in RULES:
            for stress in (1,2):
                result=replay(tuple(bars),policy,Costs(D(5)*stress,D('.0035')*stress))
                outputs.append(dict(symbol=symbol,cost_multiplier=stress,**result))
        print(f'{symbol}: {len(bars)} complete-session bars screened',flush=True)
    manifest['normalized_inputs']=[dict(file=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(root.glob('*-normalized.json'))]
    manifest['source_hashes']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path(__file__).resolve().parents[1]/'app/equity_overhaul/strategies.py',Path(__file__).resolve().parents[1]/'app/equity_overhaul/replay.py')}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (root/'screening.json').write_text(json.dumps(dict(scope='EXPLORATORY_SHORT_EXTERNAL_MINUTE_SCREEN',
        execution_authorized=False,normal_adverse_bps_per_side=5,normal_fee_per_share_per_side='.0035',
        promotion='NONE: insufficient later-period and execution validation',results=outputs),indent=2))
    for policy in RULES:
        rows=[r for r in outputs if r['policy_id']==policy and r['cost_multiplier']==1]
        ts=[t for r in rows for t in r['trades']]
        print(policy,'trades',len(ts),'wins',sum(D(t['net_r'])>0 for t in ts),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    capture(p.parse_args().output)
