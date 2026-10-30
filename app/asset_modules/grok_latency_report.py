"""Read-only numeric latency report; no prompts, answers, positions or API calls."""
import argparse
from collections import Counter
from contextlib import closing
import json
from math import ceil
from pathlib import Path
import sqlite3
from statistics import median


def summarize(root):
    path=(Path(root)/'grok-request-budget.sqlite3').resolve()
    if not path.is_file():raise ValueError('GROK_REQUEST_LEDGER_NOT_PRESENT')
    if path.stat().st_size>32*1024*1024:raise ValueError('GROK_REPORT_LEDGER_BOUND')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA application_id').fetchone()[0]!=0x41474742:
            raise ValueError('DEDICATED_GROK_REQUEST_LEDGER_REQUIRED')
        rows=db.execute('SELECT role,result FROM requests ORDER BY rowid LIMIT 21').fetchall()
    if len(rows)>20:raise ValueError('GROK_REPORT_REQUEST_BOUND')
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
    return dict(scope='READ_ONLY_GROK_LATENCY_V1',requests_in_this_ledger=len(rows),
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
    print(json.dumps(summarize(parser.parse_args().trial),indent=2))


if __name__=='__main__':main()
