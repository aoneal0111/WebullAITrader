"""Explicit bounded Grok PAPER execution lease and durable shared call budget.

This module starts no GUI, worker, provider or order. The existing native
review/controller and crypto ledger remain the only execution implementations.
"""
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import sqlite3
from time import monotonic
from uuid import uuid4
import time

from .daily_research_journal import canonical, safe
from .grok import GrokUnavailable

ENV='ATLAS_GROK_EXECUTION_TRIAL'
SCOPE='BOUNDED_GROK_PAPER_EXECUTION_V1'
ROLES={'WARRIOR_MOMENTUM_V1','QUICK_SCALPER','CRYPTO','MASTER','LEARNING'}
LIMITS=dict(paid_attempts=20,minutes=30,crypto_position_dollars='100',crypto_initial_risk='1')


def prior_attempts(root):
    """Retained intention invocations consume this trial's shared call budget."""
    total=0
    for folder in sorted(Path(root).glob('grok-order-intentions*')):
        if not folder.is_dir():continue
        path=folder/'summary.json'
        if not path.is_file() or path.stat().st_size>65536:
            raise ValueError('PREVIOUS_GROK_SUMMARY_REQUIRED_FOR_BUDGET')
        report=json.loads(path.read_text(encoding='utf-8-sig'))
        values=[report.get(k) for k in ('requests_scheduled','provider_requests_attempted') if k in report]
        if not values or any(type(v) is not int or not 0<=v<=20 for v in values):
            raise ValueError('PREVIOUS_GROK_REQUEST_ACCOUNTING_INVALID')
        total+=max(values)
    if total>20:raise ValueError('PREVIOUS_GROK_BUDGET_ALREADY_EXCEEDED')
    return total


def create(root,*,clock=lambda:datetime.now(UTC)):
    from app.equity_overhaul.paper_experiment import load
    root=Path(root).resolve();_,parent=load(root/'experiment.json',clock)
    if parent.get('profile')!='CENT_EARLY_V2':raise ValueError('CURRENT_CENT_EARLY_V2_REQUIRED')
    if not (root/'entry-attempts.sqlite3').is_file():raise ValueError('EXISTING_ENTRY_BUDGET_REQUIRED')
    now=clock();doc=dict(scope=SCOPE,id=uuid4().hex,trial_id=parent['trial_id'],
        parent_sha256=sha256(canonical(parent)).hexdigest(),created_at=now.isoformat(),
        expires_at=min(now+timedelta(minutes=30),datetime.fromisoformat(parent['expires_at'])).isoformat(),
        limits=LIMITS,prior_paid_attempts=prior_attempts(root),mode='NATIVE_CANDIDATE_REVIEW_AND_CRYPTO_PROPOSALS')
    path=root/'grok-execution-lease.json'
    with path.open('x',encoding='utf-8') as stream:json.dump(doc,stream,indent=2)
    return path,doc


class ExecutionLease:
    def __init__(self,path,*,clock=lambda:datetime.now(UTC),timer=monotonic):
        self.path=Path(path).resolve();self.root=self.path.parent;self.clock=clock
        self.capture_ready=None
        self._timer=timer;self._wall_start=clock();self._timer_start=timer()
        if self.path.stat().st_size>16384:raise ValueError('GROK_LEASE_BOUND')
        self.doc=json.loads(self.path.read_text(encoding='utf-8-sig'))
        d=self.doc
        if (set(d)!={'scope','id','trial_id','parent_sha256','created_at','expires_at','limits','prior_paid_attempts','mode'}
            or d['scope']!=SCOPE or d['limits']!=LIMITS or len(d['id'])!=32
            or type(d['prior_paid_attempts']) is not int or not 0<=d['prior_paid_attempts']<=20
            or d['mode']!='NATIVE_CANDIDATE_REVIEW_AND_CRYPTO_PROPOSALS'):
            raise ValueError('INVALID_GROK_EXECUTION_LEASE')
        self._parent=self._read_parent()
        start,end=(datetime.fromisoformat(d[k]) for k in ('created_at','expires_at'))
        if (start.utcoffset() is None or end.utcoffset() is None or not timedelta(0)<end-start<=timedelta(minutes=30)
            or end>datetime.fromisoformat(self._parent['expires_at'])):raise ValueError('INVALID_GROK_LEASE_TIME')
        self.db_path=self.root/'grok-request-budget.sqlite3'
        with closing(sqlite3.connect(self.db_path)) as db:
            marker=db.execute('PRAGMA application_id').fetchone()[0]
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if marker not in (0,0x41474742) or (marker==0 and names):raise ValueError('DEDICATED_GROK_BUDGET_REQUIRED')
            db.execute('CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY,digest TEXT NOT NULL)')
            digest=sha256(canonical(d)).hexdigest();old=db.execute('SELECT digest FROM identity WHERE id=1').fetchone()
            if old and old[0]!=digest:raise ValueError('GROK_BUDGET_IDENTITY_CONFLICT')
            db.execute('INSERT OR IGNORE INTO identity VALUES (1,?)',(digest,))
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY,role TEXT NOT NULL,at TEXT NOT NULL,context TEXT NOT NULL,result TEXT)')
            db.execute('PRAGMA application_id=0x41474742');db.commit()

    def _read_parent(self):
        from app.equity_overhaul.paper_experiment import load
        _,parent=load(self.root/'experiment.json',self.clock)
        if (parent['trial_id']!=self.doc['trial_id'] or parent.get('profile')!='CENT_EARLY_V2'
            or sha256(canonical(parent)).hexdigest()!=self.doc['parent_sha256']):raise ValueError('GROK_PARENT_TRIAL_CHANGED')
        return parent

    def active(self):
        try:
            now=self.clock();d=self.doc
            return (self.path.is_file() and json.loads(self.path.read_text(encoding='utf-8-sig'))==d
                and self._read_parent()==self._parent
                and datetime.fromisoformat(d['created_at'])<=now<datetime.fromisoformat(d['expires_at'])
                and self._timer()>=self._timer_start
                and abs((now-self._wall_start).total_seconds()-(self._timer()-self._timer_start))<=2
                and not any((self.root/name).exists() for name in ('STOP_GROK','PAUSE_ENTRIES','PAUSE_GROK_EXECUTION')))
        except Exception:return False

    def request_count(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            return self.doc['prior_paid_attempts']+db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]

    def status(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            roles=dict(db.execute('SELECT role,COUNT(*) FROM requests GROUP BY role'))
        return dict(scope=SCOPE,active=self.active(),expires_at=self.doc['expires_at'],
            paid_attempts_limit=20,paid_attempts_used=self.request_count(),requests_by_role=roles,
            mode=self.doc['mode'],backtest_promotion='NONE',broker_orders=0)

    def reserve_request(self,role,context):
        if role not in ROLES or context.get('mode')!='PAPER' or not self.active():raise GrokUnavailable('GROK_TRIAL_NOT_ACTIVE')
        raw=json.dumps(safe(context),allow_nan=False,separators=(',',':'))
        if len(raw.encode())>65536:raise GrokUnavailable('GROK_TRIAL_INPUT_BOUND')
        now=self.clock();identity=uuid4().hex
        with closing(sqlite3.connect(self.db_path,timeout=2)) as db:
            db.execute('BEGIN IMMEDIATE')
            count=self.doc['prior_paid_attempts']+db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
            previous=db.execute('SELECT at FROM requests WHERE role=? ORDER BY rowid DESC LIMIT 1',(role,)).fetchone()
            if count>=20:raise GrokUnavailable('GROK_TRIAL_PAID_LIMIT')
            if previous and not (now-datetime.fromisoformat(previous[0])).total_seconds()>=60:
                raise GrokUnavailable('GROK_TRIAL_ROLE_COOLDOWN')
            if not self.active():raise GrokUnavailable('GROK_TRIAL_REVOKED')
            db.execute('INSERT INTO requests VALUES (?,?,?,?,NULL)',(identity,role,now.isoformat(),raw));db.commit()
        return identity

    def finish_request(self,identity,result):
        clean=safe(result)
        if isinstance(result.get('usage'),dict):
            clean['usage']={k:v for k,v in result['usage'].items() if k in
                {'input_tokens','output_tokens','responses_with_usage','responses_without_usage'} and type(v) is int and v>=0}
        raw=json.dumps(clean,allow_nan=False,separators=(',',':'))
        if len(raw.encode())>65536:raw='{"status":"RESULT_BOUND"}'
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute('UPDATE requests SET result=? WHERE id=? AND result IS NULL',(raw,identity));db.commit()

    def claim_crypto(self,request,now):
        """Share the original fifty-attempt/$5000 budget, never create/reset it."""
        try:
            if (not self.active() or not self.capture_ready or self.capture_ready() is not True
                or request.engine.value!='CRYPTO' or not 0<= (now-request.quote_at).total_seconds()<=2
                or not 0<request.capital<=100 or not 0<request.loss_budget<=1 or request.quantity<=0):return False
            record=canonical(dict(owner='CRYPTO',command_id=request.command_id,plan_fingerprint=request.plan_fingerprint,
                capital=str(request.capital),risk=str(request.loss_budget))).decode()
            with closing(sqlite3.connect(self.root/'entry-attempts.sqlite3',timeout=2)) as db:
                db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT record FROM attempts WHERE trial=? AND request=?',(self.doc['trial_id'],request.command_id)).fetchone()
                if old:return old[0]==record
                rows=db.execute('SELECT notional FROM attempts WHERE trial=?',(self.doc['trial_id'],)).fetchall()
                if len(rows)>=50 or sum((D(r[0]) for r in rows),D(0))+request.capital>5000:return False
                if not self.active():return False
                db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(self.doc['trial_id'],request.command_id,str(request.capital),record));db.commit()
            return True
        except Exception:return False


class BudgetClient:
    """Shares one durable allowance across equity, crypto, Master and Learning."""
    def __init__(self,client,lease):self.client,self.lease=client,lease
    @property
    def model(self):return self.client.model
    @property
    def requests(self):return self.client.requests
    def usage(self):return self.client.usage()
    def diagnostics(self):return self.client.diagnostics()
    def request(self,role,instruction,context,schema):
        identity=self.lease.reserve_request(role,context)
        try:
            answer=self.client.request(role,instruction,context,schema)
            if not self.lease.active():raise GrokUnavailable('GROK_TRIAL_REVOKED')
        except Exception:
            self.lease.finish_request(identity,dict(status='FAILED_OR_REVOKED',diagnostics=self.client.diagnostics(),usage=self.client.usage()))
            raise
        self.lease.finish_request(identity,dict(status='ANSWER_RECEIVED',answer=answer,usage=self.client.usage(),diagnostics=self.client.diagnostics()))
        return answer


def configured(configuration,*,clock=lambda:datetime.now(UTC)):
    value=os.environ.get(ENV,'').strip()
    if not value:return None
    from app.equity_overhaul.paper_experiment import ENV as PARENT_ENV
    if (configuration.environment.value!='PAPER' or configuration.live_trading_enabled
        or not configuration.grok_paper_entries_enabled or not configuration.paper_controller_enabled
        or not configuration.daily_research_enabled or not configuration.crypto_shared_allocation_enabled
        or configuration.crypto_native_observer_enabled):raise ValueError('GROK_EXECUTION_TRIAL_FLAGS_REQUIRED')
    lease=ExecutionLease(value,clock=clock)
    if (Path(os.environ.get(PARENT_ENV,'')).resolve()!=lease.root/'experiment.json' or not lease.active()):
        raise ValueError('CURRENT_GROK_EXECUTION_PARENT_REQUIRED')
    return lease


def preflight(repo,root,*,clock=lambda:datetime.now(UTC)):
    from dotenv import dotenv_values
    from .grok import configured_client
    from app.equity_overhaul.paper_experiment import load
    root=Path(root).resolve();_,parent=load(root/'experiment.json',clock)
    if parent.get('profile')!='CENT_EARLY_V2' or not (root/'entry-attempts.sqlite3').is_file():
        raise ValueError('EXISTING_CENT_TRIAL_AND_ENTRY_BUDGET_REQUIRED')
    environment={**dotenv_values(Path(repo)/'.env'),**os.environ}
    if configured_client(environment) is None:raise ValueError('LOCAL_XAI_CONFIGURATION_REQUIRED')
    path=root/'grok-execution-lease.json'
    used=ExecutionLease(path,clock=clock).request_count() if path.exists() else prior_attempts(root)
    if used>=20:raise ValueError('SHARED_GROK_PAID_BUDGET_EXHAUSTED_NO_RESET')
    return dict(scope='GROK_EXECUTION_PREFLIGHT_NO_NETWORK',parent_trial_id=parent['trial_id'],
        parent_expires_at=parent['expires_at'],prior_paid_attempts=used,paid_attempts_remaining=20-used,
        credentials_present=True,credentials_validated_online=False,orders=0,network_requests=0)


def write_launchers(repo,root,*,clock=lambda:datetime.now(UTC)):
    from .paper_day_setup import FLAGS,quote
    repo,root=Path(repo).resolve(),Path(root).resolve()
    report=preflight(repo,root,clock=clock)
    environment={**FLAGS,'ATLAS_PAPER_CONTROLLER_ENABLED':'true','ATLAS_GROK_PAPER_ENTRIES_ENABLED':'true',
        'ATLAS_CRYPTO_SHARED_ALLOCATION_ENABLED':'true','ATLAS_CRYPTO_NATIVE_OBSERVER_ENABLED':'false',
        'CRYPTO_DISCOVERY_ENABLED':'true','CRYPTO_DISCOVERY_SYMBOLS':'BTC/USD,ETH/USD',
        'CRYPTO_INTELLIGENCE_RESEARCH_ENABLED':'true','ATLAS_CRYPTO_COMPLETED_BAR_CAPTURE_ENABLED':'true',
        'ATLAS_EQUITY_PAPER_EXPERIMENT':str(root/'experiment.json'),ENV:str(root/'grok-execution-lease.json'),
        'ATLAS_LEARNING_OUTCOME_JOURNAL_PATH':str(root/'grok-learning-outcomes.json')}
    python=repo/'.venv/Scripts/python.exe'
    common="$ErrorActionPreference = 'Stop'\n$TrialEnvironment = @{\n"+'\n'.join('    '+quote(k)+' = '+quote(v) for k,v in environment.items())+"\n}\n"
    common+="$Before = @{}\nforeach ($Name in $TrialEnvironment.Keys) { $Before[$Name] = [Environment]::GetEnvironmentVariable($Name,'Process'); [Environment]::SetEnvironmentVariable($Name,$TrialEnvironment[$Name],'Process') }\n"
    common+='Push-Location '+quote(repo)+'\ntry {\n'
    final="} finally { foreach ($Name in $Before.Keys) { [Environment]::SetEnvironmentVariable($Name,$Before[$Name],'Process') }; Pop-Location }\n"
    atlas=common+"    $Running = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -match 'app\\.gui\\.app|grok_day_proposals|grok_online_observer|bootstrap-observer\\.py' }\n"
    atlas+="    if ($Running) { throw 'Close existing Atlas and Grok intentions normally first. Nothing started.' }\n"
    atlas+='    & '+quote(python)+' -m app.asset_modules.grok_execution_trial --trial '+quote(root)+' --prepare\n'
    atlas+="    if ($LASTEXITCODE -ne 0) { throw 'Grok execution lease invalid or expired. No renewal attempted.' }\n"
    atlas+="    Write-Host 'EXPLICIT GROK PAPER EXECUTION PILOT; LIVE OFF; SHARED PAID LIMIT 20; LEASE UP TO 30 MINUTES; ORIGINAL ENTRY BUDGET RETAINED'\n"
    atlas+='    & '+quote(python)+' -m app.gui.app\n'+final
    backtest="param([string]$InputJson)\n"+common
    backtest+="    $BacktestArgs = @('-m','app.asset_modules.grok_execution_trial','--trial',"+quote(root)+",'--backtest-review','--watch')\n"
    backtest+="    if ($InputJson) { $BacktestArgs += @('--backtest-input',$InputJson) }\n"
    backtest+='    & '+quote(python)+' @BacktestArgs\n'
    backtest+="    if ($LASTEXITCODE -ne 0) { throw 'Backtest reviewer stopped. No policy promotion or order authority.' }\n"+final
    # Different filenames preserve the original native-only launchers/results.
    (root/'Start-Atlas-Grok-Execution.ps1').write_text(atlas,encoding='utf-8')
    (root/'Review-Grok-Backtest.ps1').write_text(backtest,encoding='utf-8')
    (root/'Pause-Grok-Execution.ps1').write_text("$null = New-Item -ItemType File -Force -Path "+quote(root/'PAUSE_GROK_EXECUTION')+"\nWrite-Host 'New Grok admission revoked. Keep Atlas and crypto data running for existing protection.'\n",encoding='utf-8')
    report.update(prepared_only=True,lease_created_on_manual_launch=True,live_trading_enabled=False,
        roles=dict(equity='Select immutable native candidates; cannot create arbitrary equity signals',
            crypto='Owned USD spot proposals through shared admission and local simulator',
            backtest='Review frozen ENGINE_COMPARISON_V1 inputs; no automatic policy promotion',
            execution='Master selects native equity commands; deterministic controller rechecks capital and current quotes'),
        original_trial_and_budget_unchanged=True)
    target=root/'grok-execution-preparation.json';target.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('MANUAL_GROK_ATLAS='+str(root/'Start-Atlas-Grok-Execution.ps1'))
    print('MANUAL_BACKTEST_REVIEW='+str(root/'Review-Grok-Backtest.ps1'))
    print('UPLOAD_SUMMARY='+str(target))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trial',type=Path,required=True)
    p.add_argument('--prepare',action='store_true');p.add_argument('--backtest-input',type=Path);p.add_argument('--backtest-review',action='store_true')
    p.add_argument('--repo',type=Path,default=Path.cwd());p.add_argument('--preflight',action='store_true')
    p.add_argument('--write-launchers',action='store_true');p.add_argument('--watch',action='store_true')
    a=p.parse_args()
    if a.preflight:print(json.dumps(preflight(a.repo,a.trial),indent=2));return
    if a.write_launchers:write_launchers(a.repo,a.trial);return
    if a.prepare:
        path=a.trial/'grok-execution-lease.json'
        if not path.exists():create(a.trial)
        lease=ExecutionLease(path)
        if not lease.active():raise ValueError('GROK_LEASE_NOT_ACTIVE_NO_RENEWAL')
        print(json.dumps(dict(scope=SCOPE,expires_at=lease.doc['expires_at'],paid_attempts_used=lease.request_count(),
            paid_attempts_limit=20,orders=0,network_requests=0,prepared_only=True),indent=2));return
    if not a.backtest_review:p.error('Explicit preparation or backtest review required')
    from app.configuration.loader import load_configuration
    from .grok import configured_client
    from .grok_learning import LearningJournal
    from app.configuration.environment import resolve_runtime_environment
    config=load_configuration(resolve_runtime_environment());lease=configured(config)
    if lease is None:raise ValueError('EXPLICIT_GROK_EXECUTION_LEASE_REQUIRED')
    journal=LearningJournal(lease.root/'grok-learning.sqlite3')
    if a.backtest_input:
        if a.backtest_input.stat().st_size>20_000_000:raise ValueError('BACKTEST_INPUT_BOUND')
        journal.ingest(json.loads(a.backtest_input.read_text(encoding='utf-8-sig')))
    client=configured_client(resolve_runtime_environment())
    if client is None:raise ValueError('LOCAL_XAI_CONFIGURATION_REQUIRED')
    bounded=BudgetClient(client,lease)
    while lease.active():
        result=journal.review_next(client=bounded)
        target=lease.root/'grok-backtest-status.json'
        temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(result,indent=2),encoding='utf-8');temporary.replace(target)
        print('BACKTEST_REVIEW='+str(result['state'])+'; ORDERS=0; PROMOTION=NONE',flush=True)
        print('UPLOAD_SUMMARY='+str(target),flush=True)
        if not a.watch:break
        try:time.sleep(30)
        except KeyboardInterrupt:break


if __name__=='__main__':main()
