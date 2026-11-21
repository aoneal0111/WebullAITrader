"""Read-only numeric latency report; no prompts, answers, positions or API calls."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime,timedelta
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
import sqlite3
from statistics import median
from .daily_research_journal import canonical


def summarize(root,*,window=None):
    root=Path(root).resolve();path=root/'grok-request-budget.sqlite3'
    if not path.is_file():raise ValueError('GROK_REQUEST_LEDGER_NOT_PRESENT')
    if path.stat().st_size>32*1024*1024:raise ValueError('GROK_REPORT_LEDGER_BOUND')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA application_id').fetchone()[0]!=0x41474742:
            raise ValueError('DEDICATED_GROK_REQUEST_LEDGER_REQUIRED')
        all_rows=db.execute('SELECT role,at,result FROM requests ORDER BY rowid LIMIT 21').fetchall()
        selected=None
        if window is not None:
            target=Path(window).resolve()
            if target.parent!=root or target.stat().st_size>16384:raise ValueError('BOUNDED_WINDOW_REPORT_REQUIRED')
            selected=json.loads(target.read_text(encoding='utf-8-sig'))
            anchor=json.loads((root/'grok-execution-lease.json').read_text(encoding='utf-8-sig'))
            approval=db.execute('SELECT digest,anchor,created_at,expires_at FROM execution_windows WHERE id=?',(selected['id'],)).fetchone()
            if approval!=(sha256(canonical(selected)).hexdigest(),sha256(canonical(anchor)).hexdigest(),selected['created_at'],selected['expires_at']):
                raise ValueError('APPROVED_WINDOW_REPORT_REQUIRED')
            start,end=datetime.fromisoformat(selected['created_at']),datetime.fromisoformat(selected['expires_at'])
            if start.utcoffset() is None or end.utcoffset() is None or not timedelta(0)<end-start<=timedelta(minutes=30):
                raise ValueError('WINDOW_REPORT_TIME_INVALID')
    rows=[(role,raw) for role,at,raw in all_rows if selected is None or
        (role in {'MASTER','CRYPTO','WARRIOR_MOMENTUM_V1','QUICK_SCALPER'} and start<=datetime.fromisoformat(at)<end)]
    if len(all_rows)>20:raise ValueError('GROK_REPORT_REQUEST_BOUND')
    statuses=Counter();roles=Counter();latencies=[];before=after=encoded=0
    for role,raw in rows:
        roles[role]+=1
        if raw is None:statuses['UNFINALIZED']+=1;continue
        result=json.loads(raw)
        state=result.get('status')
        statuses[state if state in {'ANSWER_RECEIVED','FAILED_OR_REVOKED','RESULT_BOUND'} else 'UNKNOWN']+=1
        latency=result.get('latency_ms')
        if type(latency) is int and latency>=0:latencies.append(latency)
        encoding=result.get('encoding',{})
        a,b=encoding.get('original_context_bytes'),encoding.get('sent_context_bytes')
        if type(a) is int and type(b) is int and a>=0 and b>=0:
            before+=a;after+=b;encoded+=1
    ordered=sorted(latencies)
    return dict(scope='READ_ONLY_GROK_WINDOW_LATENCY_V1' if selected else 'READ_ONLY_GROK_LATENCY_V1',
        window_id=selected['id'] if selected else None,requests_in_this_ledger=len(all_rows),requests_in_report=len(rows),
        requests_excluded_by_window=len(all_rows)-len(rows),
        requests_by_role=dict(roles),statuses=dict(statuses),latency_samples=len(ordered),
        requests_without_latency=len(rows)-len(ordered),
        latency_ms=(dict(min=ordered[0],median=median(ordered),p95=ordered[ceil(.95*len(ordered))-1],max=ordered[-1]) if ordered else None),
        contexts_with_encoding_metrics=encoded,original_context_bytes=before,sent_context_bytes=after,
        network_requests=0,orders=0,
        limitations=['Historical records without per-request timing are not treated as zero latency',
            'Input-byte reduction is not a measured latency improvement or token-cost calculation',
            'Requests in older intentions directories are outside this timing ledger; the shared budget still counts them',
            'A parsed answer is not an accepted decision, order acknowledgement or simulated fill'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--trial',type=Path,required=True)
    parser.add_argument('--window',type=Path);args=parser.parse_args()
    print(json.dumps(summarize(args.trial,window=args.window),indent=2))


if __name__=='__main__':main()
