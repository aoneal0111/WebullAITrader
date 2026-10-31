"""Streaming count reconciliation and ordered-label inventory; no new backtest."""
import argparse
from collections import Counter
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time

from .equity_corpus_review import REPORT,MAX_REPORT,read_json,review_report

MAX_LINE=1_000_000
MAX_BYTES=40_000_000_000
MAX_ROWS=2_000_000
LABELS={'TARGET_FIRST','STOP_FIRST','INTRABAR_ORDER_UNKNOWN'}


def scan(path,expected,expected_episodes,normalized,*,progress=print,max_rows=MAX_ROWS):
    path=Path(path);before=path.stat()
    if before.st_size>MAX_BYTES:raise ValueError('CORPUS_BYTE_LIMIT')
    counts=Counter();membership=Counter();labels={s:{t:Counter() for t in ('0.5','1','2')} for s in expected}
    digest=sha256();days=set();partition_presence={};last_progress=time.monotonic();complete=True
    with tempfile.TemporaryDirectory(prefix='atlas-corpus-ids-') as temp:
        db=sqlite3.connect(str(Path(temp)/'ids.sqlite3'))
        try:
            db.execute('CREATE TABLE seen (id TEXT PRIMARY KEY) WITHOUT ROWID')
            with path.open('rb') as stream:
                while True:
                    line=stream.readline(MAX_LINE+1)
                    if not line:break
                    if counts['rows']>=max_rows:
                        complete=False;counts['row_limit_reached']+=1;break
                    if len(line)>MAX_LINE or not line.endswith(b'\n'):raise ValueError('INCOMPLETE_OR_OVERSIZED_EPISODE_ROW')
                    digest.update(line);counts['rows']+=1
                    row=json.loads(line)
                    if not isinstance(row,dict):raise ValueError('EPISODE_OBJECT_REQUIRED')
                    identity=row.get('episode_id');members=row.get('strategy_memberships')
                    if (not isinstance(identity,str) or not 0<len(identity)<=256 or not isinstance(members,list)
                            or not 0<len(members)<=23 or any(not isinstance(s,str) for s in members)
                            or len(set(members))!=len(members) or not set(members)<=set(expected)):
                        counts['invalid_identity_or_memberships']+=1;continue
                    if db.execute('INSERT OR IGNORE INTO seen VALUES (?)',(identity,)).rowcount==0:
                        counts['duplicate_episode_ids']+=1
                    counts['valid_membership_rows']+=1
                    membership.update(members)
                    symbol=row.get('symbol');day=row.get('trading_date')
                    try:
                        if not isinstance(day,str):raise ValueError()
                        parsed=date.fromisoformat(day)
                        if parsed.isoformat()!=day or not isinstance(symbol,str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,19}',symbol):
                            raise ValueError()
                    except ValueError:counts['invalid_partition_identity']+=1
                    else:
                        days.add(day);key=(symbol,day)
                        if key not in partition_presence:
                            if len(partition_presence)>=100000:raise ValueError('PARTITION_BOUND')
                            partition_presence[key]=(Path(normalized)/f'{symbol}_{day}.jsonl').is_file()
                        counts['episode_with_normalized_partition' if partition_presence[key] else 'episode_without_normalized_partition']+=1
                    outcomes=row.get('outcomes',{})
                    if not isinstance(outcomes,dict):outcomes={}
                    targets=outcomes.get('r_targets',{})
                    if not isinstance(targets,dict):targets={}
                    for target in ('0.5','1','2'):
                        event=targets.get(target,{})
                        label=event.get('first_plan_event') if isinstance(event,dict) else None
                        label=label if isinstance(label,str) and label in LABELS else 'OTHER_OR_MISSING'
                        for strategy in members:labels[strategy][target][label]+=1
                    if counts['rows']%5000==0:db.commit()
                    if time.monotonic()-last_progress>=10:
                        progress(f'CORPUS_SCAN={path.parent.name}; ROWS={counts["rows"]}; BYTES={stream.tell()}/{before.st_size}',flush=True)
                        last_progress=time.monotonic()
            unique=db.execute('SELECT COUNT(*) FROM seen').fetchone()[0]
        finally:db.close()
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('CORPUS_CHANGED_DURING_SCAN')
    differences={s:dict(expected=expected[s],observed=membership[s]) for s in expected if expected[s]!=membership[s]}
    matches=(complete and unique==expected_episodes and not differences and not counts['invalid_identity_or_memberships']
             and not counts['duplicate_episode_ids'])
    ordered=[]
    for strategy in sorted(expected):
        cohorts=[]
        for target in ('0.5','1','2'):
            c=labels[strategy][target];n=sum(c.values());resolved=c['TARGET_FIRST']+c['STOP_FIRST']
            cohorts.append(dict(target_r=target,observations=n,counts=dict(c),
                target_first_share_of_all=c['TARGET_FIRST']/n if n else None,
                target_first_share_of_resolved=c['TARGET_FIRST']/resolved if resolved else None,
                net_trade_win_rate=None,net_expectancy=None))
        ordered.append(dict(strategy=strategy,cohorts=cohorts))
    return dict(corpus=path.parent.name,source_bytes=before.st_size,
        corpus_sha256=digest.hexdigest() if complete else None,complete_scan=complete,
        counts=dict(counts),unique_episode_ids=unique,strategy_memberships=sum(membership.values()),
        per_strategy_count_differences=differences,counts_reconcile_with_report=matches,
        identity_status='COUNT_RECONCILED_NOT_CRYPTOGRAPHIC_PROVENANCE' if matches else 'NOT_RECONCILED',
        first_date=min(days) if days else None,last_date=max(days) if days else None,
        normalized_partitions_observed=len(partition_presence),normalized_partitions_present=sum(partition_presence.values()),
        ordered_label_inventory=ordered,execution_authorized=False)


def run(repo,output,*,progress=print):
    repo=Path(repo).resolve();report_path=repo/REPORT
    report,report_hash=read_json(report_path,MAX_REPORT);review=review_report(report)
    expected={s['strategy']:s['whole_sample_memberships'] for s in review['strategies']}
    expected_episodes=report.get('metadata',{}).get('records_ingested')
    if type(expected_episodes) is not int or expected_episodes<0:raise ValueError('REPORT_EPISODE_COUNT_REQUIRED')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    tranche=report_path.parent.parent;results=[]
    try:
        for name in ('corpus','corpus_repaired'):
            source=tranche/name/'episodes.jsonl'
            if not source.is_file():
                results.append(dict(corpus=name,status='MISSING'));continue
            result=scan(source,expected,expected_episodes,tranche/'normalized',progress=progress)
            manifest_path=source.with_name('manifest.json')
            if manifest_path.is_file():
                manifest,mhash=read_json(manifest_path,1_000_000)
                result['manifest_sha256']=mhash
                summary=manifest.get('summary',{})
                result['manifest_claims']={k:summary.get(k) for k in ('accepted_unique','strategy_memberships')}
                result['manifest_counts_match_file']=(summary.get('accepted_unique')==result['unique_episode_ids']
                    and summary.get('strategy_memberships')==result['strategy_memberships'])
            results.append(result)
            (output/f'{name}-reconciliation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    except Exception as error:
        (output/'failure.json').write_text(json.dumps(dict(status='SCAN_FAILED',error_type=type(error).__name__,
            reason=str(error) if type(error) is ValueError else 'READ_OR_DATABASE_FAILURE',
            completed_corpora=[r['corpus'] for r in results],orders=0)),encoding='utf-8')
        raise
    _,report_after=read_json(report_path,MAX_REPORT)
    if report_after!=report_hash:raise ValueError('REPORT_CHANGED_DURING_SCAN')
    report_out=dict(scope='OFFLINE_CORPUS_COUNT_AND_ORDERED_LABEL_RECONCILIATION_V1',source_report_sha256=report_hash,
        expected_unique_episodes=expected_episodes,expected_memberships=sum(expected.values()),corpora=results,
        matched_corpora=[r['corpus'] for r in results if r.get('counts_reconcile_with_report')],
        orders=0,network_requests=0,grok_requests=0,execution_authorized=False,promotion='NONE',
        limitations=['Counts can reconcile without proving that these exact bytes generated the historical report',
            'Ordered labels are reused, not recalculated against bars; their correctness remains unverified',
            'Resolved-only target-first shares exclude ambiguous/missing labels and are not net trade win rates',
            'Observations overlap across strategies and episodes; no portfolio or cost backtest is performed',
            'Normalized partition presence does not verify content, completeness, entry reachability or executable quotes',
            'Existing manifests remain unchanged even when their counts are stale'])
    path=output/'corpus-reconciliation-summary.json'
    path.write_text(json.dumps(report_out,indent=2),encoding='utf-8')
    progress('UPLOAD_SUMMARY='+str(path),flush=True)
    return report_out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.repo,a.output)


if __name__=='__main__':main()
