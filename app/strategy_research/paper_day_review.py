"""Private read-only post-run package and trial-bound fill reconciliation.

No broker, model, GUI, strategy activation or source database mutation. Historical
campaign outcomes are never presented as the requested trial's performance.
"""
import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sqlite3
import time
import zipfile

from app.read_only_sqlite import read_database, bounded_rows
from app.asset_modules.learning_outcomes import read_equity_outcomes, outcome_summary, amount
from app.asset_modules.outcome_metrics import outcome_metrics


MAX_DATABASE = 512 * 1024 ** 2


def digest(path):
    h=sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def read_json(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>8*1024**2:
        raise ValueError('BOUNDED_REGULAR_REVIEW_JSON_REQUIRED:'+path.name)
    return json.loads(path.read_text(encoding='utf-8-sig'))


def snapshot(source,target):
    source,target=Path(source).resolve(),Path(target)
    if not source.is_file() or source.stat().st_size>MAX_DATABASE or target.exists():
        raise ValueError('BOUNDED_EXISTING_SOURCE_AND_NEW_SNAPSHOT_REQUIRED')
    began=time.monotonic()
    with read_database(source) as src:
        page_size=src.execute('PRAGMA page_size').fetchone()[0]
        if src.execute('PRAGMA page_count').fetchone()[0]*page_size>MAX_DATABASE:
            raise ValueError('SNAPSHOT_PAGE_BOUND')
        def progress(status,left,total):
            if time.monotonic()-began>30 or total*page_size>MAX_DATABASE:
                raise ValueError('SNAPSHOT_TIME_OR_PAGE_BOUND')
        dst=sqlite3.connect(target)
        try:src.backup(dst,pages=256,progress=progress,sleep=.01)
        finally:dst.close()
    if target.stat().st_size>MAX_DATABASE:raise ValueError('SNAPSHOT_SIZE_BOUND')
    return {'sha256':digest(target),'bytes':target.stat().st_size}


def trial_outcomes(ledger, trial_id):
    if not re.fullmatch('[0-9a-f]{32}',trial_id):raise ValueError('EXPLICIT_TRIAL_ID_REQUIRED')
    canonical=read_equity_outcomes(ledger)
    campaign=canonical['campaign']
    with read_database(ledger) as db:
        rows=bounded_rows(db,"""SELECT payload FROM orders
            WHERE COALESCE(json_extract(payload,'$.paper_campaign_id'),'legacy-paper-campaign')=?
            OR json_extract(payload,'$.request.metadata.strategy_trial_id')=?
            ORDER BY order_id LIMIT 5001""",(campaign,trial_id),5000,32_000_000)
    members=defaultdict(set);selected=set();submitted=0;missing=0
    for (raw,) in rows:
        order=json.loads(raw);request=order['request'];metadata=request.get('metadata') or {}
        if request.get('side')!='BUY':continue
        owner_trial=metadata.get('strategy_trial_id')
        if owner_trial==trial_id:submitted+=1
        if amount(order['filled_quantity'])<=0:continue
        key=(order.get('paper_campaign_id','legacy-paper-campaign'),request.get('strategy_lifecycle_id'),request['symbol'])
        members[key].add(owner_trial)
        if owner_trial==trial_id:
            if key[1] is None:missing+=1
            else:selected.add(key)
    if any(k[0]!=campaign for k in selected):raise ValueError('TRIAL_SPANS_NONACTIVE_CAMPAIGN_MANUAL_REVIEW_REQUIRED')
    mixed={k for k in selected if members[k]!={trial_id}}
    eligible=selected-mixed
    outcomes=[o for o in canonical['outcomes'] if (o['campaign'],o['lifecycle'],o['instrument']) in eligible]
    closed={(o['campaign'],o['lifecycle'],o['instrument']) for o in outcomes}
    return {'selection':'EXPLICIT_FILLED_BUY_STRATEGY_TRIAL_ID_AND_RECONCILED_LIFECYCLE',
        'trial_id':trial_id,'campaign':campaign,'submitted_buy_orders':submitted,
        'filled_trial_lifecycles':len(selected),'closed_lifecycles':len(outcomes),
        'overall':outcome_metrics([amount(o['net_pnl']) for o in outcomes]),
        'groups':outcome_summary(outcomes)['groups'],'outcomes':outcomes,
        'unreconciled_trial_lifecycles':[list(k) for k in sorted(eligible-closed)],
        'mixed_trial_lifecycles':[list(k) for k in sorted(mixed)],
        'filled_trial_buys_missing_lifecycle':missing,
        'campaign_exclusions_not_assigned_to_this_trial':canonical['excluded'],
        'limitations':['Only fully reconciled closed lifecycles; open/unbalanced/ambiguous trades are not zero-PnL results',
            'Recorded commissions only; no extra spread/slippage cost invented',
            'Simulator ledger provenance is unverified; these are paper outcomes, not live results']}


def call_reviews(rows):
    """Model answers and original deadlines only; never execution outcomes."""
    reviews=[]
    for engine,stage,at,context,raw in rows:
        def object_value(value):
            try:
                result=json.loads(value) if value else {}
                if not isinstance(result,dict):raise ValueError()
                return result,'PRESENT' if value else 'ABSENT'
            except (ValueError,TypeError):return {},'INVALID_JSON_OBJECT'
        result,result_status=object_value(raw)
        evidence,context_status=object_value(context)
        answer=result.get('answer')
        if not isinstance(answer,dict):answer={}
        candidates=evidence.get('candidates',[])
        if not isinstance(candidates,list):candidates=[]
        reason=answer.get('reason')
        reviews.append(dict(engine=engine,stage=stage,started_at=at,
            status=result.get('status','UNFINALIZED' if not raw else 'UNKNOWN'),
            result_decode_status=result_status,context_decode_status=context_status,
            latency_ms=result.get('latency_ms'),failure_category=result.get('failure_category'),
            reason_code=result.get('reason_code'),action=answer.get('action'),symbol=answer.get('symbol'),
            selected_ids=answer.get('selected_ids'),reason=reason if isinstance(reason,str) else None,
            candidates=[{k:c.get(k) for k in ('engine','candidate_id','valid_until')} for c in candidates if isinstance(c,dict)],
            model_selection_is_not_execution=True))
    return reviews


def require_stopped_desktop():
    """Inspect actual Windows processes; saved writer_alive flags are historical."""
    if sys.platform != 'win32':
        raise ValueError('WINDOWS_PROCESS_CHECK_REQUIRED_FOR_INTERRUPTED_REVIEW')
    script = r"""$ErrorActionPreference='Stop'
$p=@(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py|grok_day_proposals'
})
$p.Count
"""
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        capture_output=True,text=True,timeout=30,check=True)
    if result.stdout.strip() != '0':
        raise ValueError('ACTIVE_ATLAS_OR_OBSERVER_PROCESS_INTERRUPTED_REVIEW_REFUSED')


def verify_interrupted_prefix(session, manifest):
    """Verify only the durable checkpoint prefix; never copy or finalize raw history."""
    journal=session/'journal'
    checkpoint=read_json(journal/'journal-status.json')
    baseline=manifest.get('baseline_sha256')
    if not isinstance(baseline,str) or not re.fullmatch('[0-9a-f]{64}',baseline):
        raise ValueError('MANIFEST_BASELINE_REQUIRED')
    if checkpoint.get('baseline_sha256')!=baseline:
        raise ValueError('CHECKPOINT_BASELINE_MISMATCH')
    wanted=checkpoint.get('counts',{}).get('written')
    expected_bytes=checkpoint.get('bytes_written')
    if (type(wanted) is not int or not 0<=wanted<=8_000_000 or
            type(expected_bytes) is not int or not 0<=expected_bytes<=4*1024**3):
        raise ValueError('CHECKPOINT_BOUND')
    previous='0'*64; sequence=0; total=0; counts=Counter(); segments=[]
    paths=sorted(journal.glob('events-*.jsonl'))
    if journal.is_symlink() or len(paths)>4096:raise ValueError('JOURNAL_DIRECTORY_BOUND_OR_LINK')
    for index,path in enumerate(paths,1):
        if sequence==wanted:break
        if path.name!=f'events-{index:05d}.jsonl' or path.is_symlink():
            raise ValueError('JOURNAL_SEGMENT_SEQUENCE_OR_LINK')
        h=sha256(); size=0
        with path.open('rb') as stream:
            while sequence<wanted:
                line=stream.readline(128*1024+1)
                if not line:break
                if len(line)>128*1024 or not line.endswith(b'\n'):
                    raise ValueError('JOURNAL_PARTIAL_OR_OVERSIZE_RECORD')
                row=json.loads(line); claimed=row.pop('sha256'); sequence+=1; total+=len(line)
                canonical=json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
                if (total>expected_bytes or row['schema']!='ATLAS_DAILY_EVIDENCE_V1'
                        or row['sequence']!=sequence or row['previous_sha256']!=previous
                        or row['baseline_sha256']!=baseline or sha256(canonical).hexdigest()!=claimed):
                    raise ValueError('JOURNAL_CHAIN_MISMATCH')
                previous=claimed; counts[row['kind']]+=1; h.update(line); size+=len(line)
        segments.append(dict(name=path.name,bytes=size,sha256=h.hexdigest()))
        print('REVIEW_PREFIX_RECORDS='+str(sequence),flush=True)
    if sequence!=wanted or total!=expected_bytes or previous!=checkpoint.get('chain_head'):
        raise ValueError('CHECKPOINT_PREFIX_MISMATCH')
    return checkpoint,dict(scope='INTERRUPTED_SESSION_WRITTEN_PREFIX_REVIEW_V1',
        records=sequence,bytes=total,chain_head=previous,baseline_sha256=baseline,
        kinds=dict(counts),segments=segments,checkpoint_at=checkpoint.get('updated_at'),
        final_summary_present=False,full_run_complete=False,
        tail_after_checkpoint='EXCLUDED_UNVERIFIED',
        writer_alive_is_historical_observation=True)


def collect(trial,ledger,output,*,interrupted=False):
    trial,ledger,output=Path(trial).resolve(),Path(ledger).resolve(),Path(output).resolve()
    spec=read_json(trial/'experiment.json');pointer=read_json(trial/'recording-session.json')
    if pointer['trial_id']!=spec['trial_id']:raise ValueError('TRIAL_POINTER_MISMATCH')
    session=Path(pointer['session']);manifest=read_json(session/'research-manifest.json')
    summary=session/'research-summary.json'
    if interrupted:
        require_stopped_desktop()
        if summary.exists():raise ValueError('SUMMARY_PRESENT_USE_NORMAL_REVIEW')
        watched=[trial/'experiment.json',trial/'recording-session.json',
            session/'research-manifest.json',session/'journal/journal-status.json']
        watched_hashes={p:digest(p) for p in watched}
        def journal_state():
            return [(p.name,p.stat().st_size,p.stat().st_mtime_ns)
                for p in sorted((session/'journal').glob('events-*.jsonl'))]
        segment_state=journal_state()
        checkpoint,prefix=verify_interrupted_prefix(session,manifest)
        final={'journal':checkpoint,'reason':'INTERRUPTED_NO_FINAL_SUMMARY'}
    else:
        final=read_json(summary)
    if manifest.get('paper_experiment')!=spec:raise ValueError('NATIVE_MANIFEST_TRIAL_MISMATCH')
    if not interrupted and (not final.get('journal',{}).get('closed') or not final['journal'].get('writer_stopped')):
        raise ValueError('FINALIZED_RECORDING_REQUIRED')
    if output.exists():raise ValueError('NEW_PRIVATE_REVIEW_DIRECTORY_REQUIRED')
    sources=[ledger,trial/'entry-attempts.sqlite3',trial/'grok-team-budget.sqlite3']
    required=2*sum(p.stat().st_size for source in sources for p in (source,Path(str(source)+'-wal')) if p.is_file())+256*1024**2
    if shutil.disk_usage(output.parent).free<required:raise ValueError('PRIVATE_REVIEW_FREE_SPACE_REQUIRED')
    output.mkdir(parents=True)
    payload=output/'review';payload.mkdir()
    files={}
    def copy(source,relative):
        source=Path(source)
        if not source.exists():return
        if source.is_symlink() or source.stat().st_size>8*1024**2:
            raise ValueError('REVIEW_FILE_BOUND:'+source.name)
        target=payload/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        files[relative]={'bytes':target.stat().st_size,'sha256':digest(target)}
    databases=[(ledger,'native/paper-ledger.sqlite3'),
        (trial/'entry-attempts.sqlite3','trial/entry-attempts.sqlite3'),
        (trial/'grok-team-budget.sqlite3','grok/grok-team-budget.sqlite3')]
    for source,relative in databases:
        if not source.is_file():
            if source==ledger:raise ValueError('NATIVE_PAPER_LEDGER_REQUIRED')
            continue
        target=payload/relative;target.parent.mkdir(parents=True,exist_ok=True)
        files[relative]=snapshot(source,target)
    for name in ('experiment.json','preparation-summary.json','recording-session.json','warrior-control-status.json'):
        copy(trial/name,'trial/'+name)
    for name in ('research-manifest.json','research-summary.json','engine-readiness.json'):
        copy(session/name,'capture/'+name)
    copy(session/'journal/journal-status.json','capture/journal-status.json')
    for run in sorted((trial/'grok-team-observer').glob('run-*')):
        if not run.is_dir() or run.is_symlink():continue
        for name in ('summary.json','observer-preflight.json','observer-source.json','input-health.json','private-proposals.jsonl'):
            copy(run/name,'grok/'+run.name+'/'+name)
    report={'scope':'PRIVATE_CLOSED_PAPER_DAY_REVIEW_V1','collected_at':datetime.now(UTC).isoformat(),
        'trial_id':spec['trial_id'],'recording_session':str(session),'orders':0,'network_requests':0,
        'execution_authorized':False,'native_database_modified':False,
        'capture':{k:final['journal'].get(k) for k in ('closed','writer_stopped','writer_failed','capacity_reached','evidence_complete','bytes_written')},
        'capture_reason':final.get('reason'),'files':files,
        'entry_diagnostics':final.get('entry_diagnostics'),
        'paired_scalper_quote_comparison':final.get('context_capture',{}).get('scalper_confirmation_comparison'),
        'limitations':['Raw journal stays at its original location; this package contains final summaries and ledger snapshots',
            'Capture completeness is reported by its writer; the full journal hash chain is not rescanned here',
            'Grok intentions have no separate filled-position ledger or measurable win rate']}
    try:report['native_trial']=trial_outcomes(payload/'native/paper-ledger.sqlite3',spec['trial_id'])
    except (ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        report['native_trial']={'status':'RECONCILIATION_FAILED','error_type':type(exc).__name__}
    budget=payload/'grok/grok-team-budget.sqlite3'
    if budget.is_file():
        with read_database(budget) as db:
            rows=bounded_rows(db,'SELECT engine,stage,started,context,result FROM calls ORDER BY rowid LIMIT 61',(),60,8*1024**2)
        report['grok_roles']=dict(Counter(engine+'|'+stage for engine,stage,_,_,_ in rows))
        report['grok_reviews']=call_reviews(rows)
        report['news_results']=[{'at':at,'status':r.get('status','UNFINALIZED'),
            'provider_status':r.get('provider_status'),'failure_category':r.get('failure_category'),
            'http_status':r.get('http_status'),'citation_count':len(r.get('citations',[])),
            'latency_ms':r.get('latency_ms')} for engine,stage,at,_,raw in rows
            if engine=='NEWS_DESK' and stage=='NEWS' for r in [json.loads(raw) if raw else {}]]
    if interrupted:
        require_stopped_desktop()
        if (summary.exists() or journal_state()!=segment_state
                or any(digest(p)!=value for p,value in watched_hashes.items())):
            raise ValueError('RECORDING_CHANGED_DURING_INTERRUPTED_REVIEW')
        report['scope']='PRIVATE_INTERRUPTED_PAPER_SESSION_REVIEW_V1'
        report['verified_prefix']=prefix
        report['limitations']=[
            'Interrupted session: original unclosed checkpoint preserved; writer_alive is a stale observation, not process health',
            'Only checkpoint-written records verified; queued records and all later bytes are excluded and unverified',
            'Ledger snapshots are consistent separately; journal and ledger are not one atomic snapshot',
            'No final entry diagnostics exist; event-kind counts do not establish unique trades or final engine totals',
            'Grok intentions are decisions, not filled trades or measurable win rate']
    (payload/'paper-day-review.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    archive=output/('interrupted-paper-session-review.zip' if interrupted else 'closed-paper-day-review.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(payload.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(payload).as_posix())
    return report,archive


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trial',required=True,type=Path);p.add_argument('--ledger',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--interrupted',action='store_true',help='Missing final summary; verify stopped Windows producers and saved journal prefix')
    a=p.parse_args();report,archive=collect(a.trial,a.ledger,a.output,interrupted=a.interrupted)
    result=report['native_trial'];metrics=result.get('overall',{})
    print('REVIEW=COMPLETE; ORDERS=0; API_REQUESTS=0')
    print('TRIAL_CLOSED='+str(result.get('closed_lifecycles'))+'; WINS='+str(metrics.get('wins'))+
        '; LOSSES='+str(metrics.get('losses'))+'; NET_PNL='+str(metrics.get('net_pnl')))
    print('UPLOAD_FILE='+str(archive))


if __name__=='__main__':main()
