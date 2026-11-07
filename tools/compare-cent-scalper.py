"""Offline single-symbol quote JSONL comparison. No network or order clients."""
import argparse
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.equity_overhaul.cent_scalper import Quote, Rules, replay


def read_quotes(path):
    if path.stat().st_size > 16*1024*1024:
        raise ValueError('input exceeds 16 MiB')
    with path.open(encoding='utf-8') as source:
        for count,line in enumerate(source,1):
            if count > 100000 or len(line) > 16000:
                raise ValueError('quote row bound exceeded')
            row=json.loads(line)
            for key in ('received_at','quoted_at'):
                row[key]=datetime.fromisoformat(row[key].replace('Z','+00:00'))
            for key in ('bid','ask','bid_size','ask_size'):
                row[key]=Decimal(str(row[key]))
            yield Quote(**row)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--quotes',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    if args.output.exists():
        p.error('output exists; use a new file')
    if args.quotes.stat().st_size > 16*1024*1024:
        p.error('input exceeds 16 MiB')
    input_hash=hashlib.sha256(args.quotes.read_bytes()).hexdigest()
    # Load once so both fixed cases see exactly the same immutable evidence.
    quotes=tuple(read_quotes(args.quotes))
    if hashlib.sha256(args.quotes.read_bytes()).hexdigest()!=input_hash:
        raise ValueError('input changed during read')
    source=Path(__file__).resolve().parents[1]/'app/equity_overhaul/cent_scalper.py'
    cases=[]
    for target in (Decimal('.05'),Decimal('.10')):
        rules=Rules(target=target)
        cases.append(dict(rules=asdict(rules),result=replay(quotes,rules)))
    report=dict(scope='OFFLINE_CENT_SCALPER_COMPARISON_V1',orders=0,network_requests=0,
        baseline_modified=False,promotion='NONE',input_quotes=len(quotes),
        quote_file_sha256=input_hash,
        implementation_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),cases=cases,
        limitations=['Hypothetical marketable fills; no latency/order-book/queue simulation',
                    'Input provenance, session states and completeness require separate verification',
                    'One symbol; no portfolio buying-power or shared-risk simulation',
                    'Open positions remain open and are excluded from closed-trade net',
                    'No future-data fitting or strategy selection; neither case is qualified'])
    with args.output.open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2,default=str)
    print('OFFLINE_COMPARISON_WRITTEN; PROMOTION=NONE; ORDERS=0')


if __name__=='__main__':main()
