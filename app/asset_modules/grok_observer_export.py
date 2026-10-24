"""Bounded read-only market-evidence archive. Never an online observer input."""
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import argparse
import json
from pathlib import Path
import re

from app.read_only_sqlite import bounded_rows, read_database

ROW_LIMIT=1000
SEQUENCE_WINDOW=20000
BYTE_LIMIT=8*1024*1024
BASELINE_FILES=('app/strategies/warrior_momentum/strategy.py',
    'app/strategies/warrior_momentum/quick_scalper.py',
    'app/strategies/warrior_momentum/configuration.py','app/equity_overhaul/admission.py')


def _time(value):
    if not isinstance(value,str):raise ValueError('Recorded timestamp required')
    at=datetime.fromisoformat(value)
    if at.utcoffset() is None:raise ValueError('Aware recorded timestamp required')
    return at


def _number(value,*,zero=False):
    if not isinstance(value,(str,int)) or isinstance(value,bool):raise ValueError('Exact source decimal required')
    n=Decimal(value)
    if (not n.is_finite() or n<0 or (not zero and n==0) or len(n.as_tuple().digits)>24
            or n.adjusted()>12 or n.as_tuple().exponent < -8):raise ValueError('Bounded source decimal required')
    return str(n)


def _unique(items):
    result={}
    for key,value in items:
        if key in result:raise ValueError('Duplicate JSON field')
        result[key]=value
    return result


def archive(repo,source,directory):
    repo,source,directory=Path(repo),Path(source),Path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    summary=dict(scope='READ_ONLY_GROK_MARKET_EVIDENCE_ARCHIVE_V1',
        status='SOURCE_MISSING',online_observer_ready=False,orders=0,network_requests=0,grok_requests=0,
        baseline_modified=False,source_opened_read_only=False,rows_read=0,rejected_rows=0,
        bars_exported=0,quotes_exported=0,scan_limited=False,selected_rows_truncated=False,
        source_code_hashes={},files=[],
        blockers=['INDEPENDENT_BID_ASK_TIMESTAMP_PROVENANCE_MISSING',
                  'COMPLETE_WARRIOR_AND_SCALPER_CONTEXT_NOT_RECORDED',
                  'RECORDED_HISTORY_IS_NOT_FRESH_ONLINE_CONTEXT'],
        limitations=['Execution paths cover owned positions, not all scanner opportunities',
                     'Recorded observation time is not independently proven durable availability',
                     'Source-code hashes do not attest effective runtime settings or baseline decisions',
                     'Archive omits accounts, fills, quantities, PnL, lifecycle IDs and arbitrary text'])
    for name in BASELINE_FILES:
        path=repo/name
        if path.is_file():summary['source_code_hashes'][name]=sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    records=[]
    try:
        if source.is_file():
            with read_database(source) as db:
                summary['source_opened_read_only']=True
                db.execute('BEGIN')
                if db.execute('SELECT schema_version FROM capture_metadata WHERE singleton=1').fetchone()!=(1,):
                    raise ValueError('Unknown capture schema')
                columns=db.execute('PRAGMA table_info(capture_records)').fetchall()
                if not any(c[1]=='sequence' and c[2].upper()=='INTEGER' and c[5]==1 for c in columns):
                    raise ValueError('Bounded integer sequence schema required')
                last=db.execute('SELECT sequence FROM capture_records ORDER BY sequence DESC LIMIT 1').fetchone()
                high=0 if last is None else last[0]
                low=max(0,high-SEQUENCE_WINDOW)
                summary['scan_limited']=low>0
                rows=bounded_rows(db,
                    'SELECT record_type,symbol,timestamp,CASE WHEN length(payload_json)>16384 THEN NULL ELSE payload_json END FROM capture_records NOT INDEXED '
                    'WHERE sequence>? AND sequence<=? AND record_type IN (?,?) '
                    'ORDER BY sequence DESC LIMIT ?',
                    (low,high,'MINUTE_BAR','EXECUTION_PRICE_PATH',ROW_LIMIT+1),ROW_LIMIT+1,BYTE_LIMIT)
                summary['selected_rows_truncated']=len(rows)>ROW_LIMIT
                summary['rows_read']=len(rows)
                for kind,symbol,observed,payload in reversed(rows[:ROW_LIMIT]):
                    try:
                        if not isinstance(symbol,str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,15}',symbol):
                            raise ValueError('Canonical equity symbol required')
                        if len(payload.encode())>16384:raise ValueError('Oversized capture payload')
                        p=json.loads(payload,object_pairs_hook=_unique)
                        if not isinstance(p,dict):raise ValueError('Object payload required')
                        at=_time(observed)
                        row=dict(symbol=symbol,recorded_observation_at=at.isoformat(),
                                 source_payload_sha256=sha256(payload.encode()).hexdigest())
                        if kind=='MINUTE_BAR':
                            if p.get('completed') is not True or p.get('interval')!='1m':raise ValueError('Completed M1 bar required')
                            opened,closed=_time(p['bar_timestamp']),_time(p['completion_timestamp'])
                            if opened.second or opened.microsecond or closed-opened!=timedelta(minutes=1) or closed>at:
                                raise ValueError('Causal minute bar required')
                            row.update(kind='RECORDED_BAR',opened_at=opened.isoformat(),closed_at=closed.isoformat(),
                                **{k:_number(p[k],zero=k=='volume') for k in ('open','high','low','close','volume')})
                            o,h,l,c=(Decimal(row[k]) for k in ('open','high','low','close'))
                            if not l<=min(o,c)<=max(o,c)<=h:raise ValueError('Invalid OHLC')
                            summary['bars_exported']+=1
                        else:
                            if p.get('action')!='QUOTE':continue
                            timestamp=_time(p['quote_timestamp'])
                            bid,ask=_number(p['bid']),_number(p['ask'])
                            if Decimal(bid)>Decimal(ask) or timestamp>at:raise ValueError('Invalid or future quote')
                            lifecycle=p.get('lifecycle_id')
                            owner=next((engine for engine in ('WARRIOR_MOMENTUM_V1','QUICK_SCALPER')
                                if isinstance(lifecycle,str) and lifecycle.startswith(engine+'|')),None)
                            row.update(kind='RECORDED_AGGREGATE_QUOTE',bid=bid,ask=ask,
                                aggregate_quote_at=timestamp.isoformat(),quote_age_at_recording_seconds=(at-timestamp).total_seconds(),
                                owner_from_recorded_lifecycle=owner,provider_source='UNRECORDED',
                                independent_side_timestamps='UNAVAILABLE',setup_state='UNRECORDED',baseline_result='UNRECORDED')
                            summary['quotes_exported']+=1
                        records.append(row)
                    except (ValueError,KeyError,TypeError,ArithmeticError,AttributeError):summary['rejected_rows']+=1
            summary['status']='ARCHIVE_ONLY_NOT_ONLINE_READY' if records else 'NO_USABLE_MARKET_EVIDENCE'
    except Exception as exc:
        summary.update(status='SOURCE_READ_FAILED',error_type=type(exc).__name__)
        # Partial rows cannot be mistaken for a complete export after an exception.
        records=[];summary['bars_exported']=summary['quotes_exported']=0
    if records:
        data=''.join(json.dumps(r,sort_keys=True)+'\n' for r in records).encode()
        with (directory/'private-market-evidence.jsonl').open('xb') as stream:stream.write(data)
        summary['files'].append(dict(name='private-market-evidence.jsonl',sha256=sha256(data).hexdigest(),records=len(records)))
    with (directory/'summary.json').open('x',encoding='utf-8') as stream:json.dump(summary,stream,indent=2)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--source',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    source=args.source or args.repo/'data/warrior_momentum_v1_forward/forward_capture.sqlite3'
    report=archive(args.repo,source,args.output)
    print('OBSERVER_EXPORT='+report['status'])
    print('UPLOAD_SUMMARY='+str((args.output/'summary.json').resolve()))
    print('PRIVATE_MARKET_EVIDENCE_STAYS_LOCAL; ONLINE_TRIAL=NOT_ENABLED; BASELINE=UNCHANGED')
    return 0 if report['status']=='ARCHIVE_ONLY_NOT_ONLINE_READY' else 2


if __name__=='__main__':raise SystemExit(main())
