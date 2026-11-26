"""Entry-only research on frozen candles. Never edits workers or places orders."""
import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path

EQUITY_LEVELS={'RSI2_SPY_REGIME':(5,7.5,10,15),'CONNORS_RSI2':(10,12.5,15,20)}
CRYPTO_LEVELS=(.08,.06,.04,.03,.02,.01)
PERIODS={'development':('2022-01-01','2024-01-01'),
         'validation':('2024-01-01','2025-01-01'),
         'assessment':('2025-01-01','2025-05-01')}


def review(inputs,output):
    import pandas as pd
    from app.strategy_lab.equity_daily_paper import parse,SYMBOLS
    from app.strategy_research.source_candidate_lab import replay as equity_replay
    from app.strategy_lab.broad_strategy_lab import indicators,Case,replay,compact
    root=Path(inputs);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    locks={};rows={}
    for symbol in SYMBOLS:
        path=root/'equity/data'/(symbol+'.json');raw=path.read_bytes()
        locks[str(path.relative_to(root))]=sha256(raw).hexdigest()
        rows[symbol],_=parse(raw,datetime(2026,11,9,tzinfo=UTC))
    report=dict(scope='ENTRY_ONLY_THRESHOLD_REVIEW_V1',equity=[],crypto=[],
        workers_modified=False,orders=0,promotion='NONE',
        limitations=['Exploratory reuse of previously seen periods; no untouched holdout',
          'Daily equity proxies are separate from native Warrior and Scalper',
          'Crypto screening uses conservative ATR-stop-first bar assumptions, not native fills',
          'Historical Binance USDT versus recent Kraken USD is a venue/universe change',
          'Profit and 60 percent wins are not guaranteed by parameter changes'])
    for stage,(start,end) in {**PERIODS,'recent':('2025-05-01','2026-11-07')}.items():
        selected={s:{d:b for d,b in bars.items() if d<end} for s,bars in rows.items()}
        if not any(d>=start for d in selected['SPY']):
            report.setdefault('excluded_periods',[]).append(dict(market='equity',stage=stage,
                reason='NO_EVALUATION_BARS',last_input=max(selected['SPY'])))
            continue
        for case,levels in EQUITY_LEVELS.items():
            for level in levels:
                r=equity_replay(selected,case,start,entry_rsi=level,include_curve=True)
                report['equity'].append(dict(stage=stage,entry_rsi=level,**{k:v for k,v in r.items()
                    if k not in ('trades','curve','events','open_positions','unexecuted_final_signals')}))
    for stage,(start,end) in {**PERIODS,'recent':('2026-10-19','2026-11-08')}.items():
        folder=root/('current-kraken' if stage=='recent' else 'historical-crypto-data')
        frames={}
        for path in sorted(folder.glob('*-1h.feather')):
            locks[str(path.relative_to(root))]=sha256(path.read_bytes()).hexdigest()
            f=pd.read_feather(path);f=f[pd.to_datetime(f.date,utc=True)<pd.Timestamp(end,tz='UTC')].copy()
            f=indicators(f)
            f['eligible']=f.index>=200
            # A discontinuity requires a fresh full warmup, never invented candles.
            f['eligible'] &= f.date.diff().eq(pd.Timedelta(hours=1)).rolling(200,min_periods=200).sum().eq(200)
            frames[path.name.split('-')[0]]=f
        calendar=sorted({at for f in frames.values() for at in f.date
            if pd.Timestamp(start,tz='UTC')<=at<pd.Timestamp(end,tz='UTC')})
        for threshold in CRYPTO_LEVELS:
            for fee in (.004,.008):
                r=replay(frames,Case('ROC3_PULLBACK',threshold),calendar,fee=fee,slip=.0005)
                report['crypto'].append(dict(stage=stage,entry_drop=threshold,**compact(r)))
        print('ENTRY_REVIEW_STAGE='+stage,flush=True)
    report['input_sha256']=locks
    (out/'review.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();review(a.inputs,a.output)


if __name__=='__main__':main()
