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


def collect(trial,ledger,output):
    trial,ledger,output=Path(trial).resolve(),Path(ledger).resolve(),Path(output).resolve()
    spec=read_json(trial/'experiment.json');pointer=read_json(trial/'recording-session.json')
    if pointer['trial_id']!=spec['trial_id']:raise ValueError('TRIAL_POINTER_MISMATCH')
    session=Path(pointer['session']);manifest=read_json(session/'research-manifest.json')
    final=read_json(session/'research-summary.json')
    if manifest.get('paper_experiment')!=spec:raise ValueError('NATIVE_MANIFEST_TRIAL_MISMATCH')
    if not final.get('journal',{}).get('closed') or not final['journal'].get('writer_stopped'):
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
    for name in ('experiment.json','preparation-summary.json','recording-session.json'):
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
        'limitations':['Raw journal stays at its original location; this package contains final summaries and ledger snapshots',
            'Capture completeness is reported by its writer; the full journal hash chain is not rescanned here',
            'Grok intentions have no separate filled-position ledger or measurable win rate']}
    try:report['native_trial']=trial_outcomes(payload/'native/paper-ledger.sqlite3',spec['trial_id'])
    except (ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        report['native_trial']={'status':'RECONCILIATION_FAILED','error_type':type(exc).__name__}
    budget=payload/'grok/grok-team-budget.sqlite3'
    if budget.is_file():
        with read_database(budget) as db:
            rows=bounded_rows(db,'SELECT engine,stage,started,result FROM calls ORDER BY rowid LIMIT 61',(),60,8*1024**2)
        report['grok_roles']=dict(Counter(engine+'|'+stage for engine,stage,_,_ in rows))
        report['news_results']=[{'at':at,'status':r.get('status','UNFINALIZED'),
            'provider_status':r.get('provider_status'),'failure_category':r.get('failure_category'),
            'http_status':r.get('http_status'),'citation_count':len(r.get('citations',[])),
            'latency_ms':r.get('latency_ms')} for engine,stage,at,raw in rows
            if engine=='NEWS_DESK' and stage=='NEWS' for r in [json.loads(raw) if raw else {}]]
    (payload/'paper-day-review.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    archive=output/'closed-paper-day-review.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(payload.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(payload).as_posix())
    return report,archive


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trial',required=True,type=Path);p.add_argument('--ledger',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();report,archive=collect(a.trial,a.ledger,a.output)
    result=report['native_trial'];metrics=result.get('overall',{})
    print('REVIEW=COMPLETE; ORDERS=0; API_REQUESTS=0')
    print('TRIAL_CLOSED='+str(result.get('closed_lifecycles'))+'; WINS='+str(metrics.get('wins'))+
        '; LOSSES='+str(metrics.get('losses'))+'; NET_PNL='+str(metrics.get('net_pnl')))
    print('UPLOAD_FILE='+str(archive))


if __name__=='__main__':main()
