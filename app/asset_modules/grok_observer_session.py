"""Prepare a private copy of the installed baseline; manual bounded capture only."""
from dataclasses import asdict
from datetime import UTC,datetime
from hashlib import sha256
import argparse
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event,Lock,Thread
from time import monotonic

HOOK_FILES=frozenset({'app/composition/desktop.py','app/strategies/warrior_momentum/desktop_sidecar.py',
    'app/strategies/warrior_momentum/forward_runtime.py','app/strategies/warrior_momentum/quick_scalper_runtime.py'})
OBSERVER_FILES=('grok_observer.py','grok_observer_context.py','grok_observer_capture.py',
    'grok_observer_runtime_tap.py','grok_observer_session.py')
NETWORK_BLOCK_CODE="""
import socket
def _forbid_observer_check_network(*args,**kwargs):
    raise RuntimeError('NETWORK_DISABLED_DURING_OFFLINE_PREPARATION')
socket.socket.connect=_forbid_observer_check_network
socket.socket.connect_ex=_forbid_observer_check_network
socket.socket.sendto=_forbid_observer_check_network
socket.create_connection=_forbid_observer_check_network
socket.getaddrinfo=_forbid_observer_check_network
"""


class PreparationError(ValueError):pass


class ModelRequestBlocker:
    """Session-local hard block; no keys, model transports or execution methods."""
    def __init__(self):
        from .grok import GrokClient,GrokUnavailable
        self._client_type=GrokClient;self._original=GrokClient.request
        self._lock=Lock();self.attempts=0
        def disabled(client,*args,**kwargs):
            with self._lock:self.attempts+=1
            raise GrokUnavailable('MODEL_DISABLED_DURING_PASSIVE_CAPTURE')
        GrokClient.request=disabled

    def close(self):
        self._client_type.request=self._original


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def source_manifest(root):
    root=Path(root);result={};size=0
    if (root/'app').is_symlink() or (hasattr(root/'app','is_junction') and (root/'app').is_junction()):
        raise PreparationError('SOURCE_LINK_REFUSED')
    for path in sorted((root/'app').rglob('*')):
        if '__pycache__' in path.parts or path.suffix in {'.pyc','.pyo'} or path.name=='.env':continue
        if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
            raise PreparationError('SOURCE_LINK_REFUSED')
        if not path.is_file():continue
        size+=path.stat().st_size
        if len(result)>=10000 or size>64*1024*1024:raise PreparationError('SOURCE_COPY_BOUND_EXCEEDED')
        result[path.relative_to(root).as_posix()]=sha256(path.read_bytes()).hexdigest()
    if not result:raise PreparationError('INSTALLED_APP_SOURCE_MISSING')
    return result


def apply_hooks(root,patch):
    """Only exact unique context replacements; refuse ambiguous or changed code."""
    lines=patch.splitlines(keepends=True);index=0;path=None;touched=set()
    while index<len(lines):
        line=lines[index]
        if line.startswith('diff --git '):
            parts=line.strip().split()
            if len(parts)!=4 or parts[2][2:]!=parts[3][2:] or parts[2][2:] not in HOOK_FILES:
                raise PreparationError('OBSERVER_PATCH_PATH_REFUSED')
            path=parts[2][2:];index+=1;continue
        if not line.startswith('@@ '):index+=1;continue
        if path is None:raise PreparationError('OBSERVER_PATCH_INVALID')
        old=[];new=[];index+=1
        while index<len(lines) and not lines[index].startswith(('@@ ','diff --git ')):
            item=lines[index]
            if item.startswith(' '):old.append(item[1:]);new.append(item[1:])
            elif item.startswith('-'):old.append(item[1:])
            elif item.startswith('+'):new.append(item[1:])
            elif not item.startswith('\\ No newline'):raise PreparationError('OBSERVER_PATCH_INVALID')
            index+=1
        old,new=''.join(old),''.join(new)
        target=Path(root)/path
        if not target.is_file():raise PreparationError('OBSERVER_PATCH_BASELINE_MISMATCH')
        text=target.read_text(encoding='utf-8')
        if old and text.count(old)==1:text=text.replace(old,new,1)
        elif new and text.count(new)==1 and text.count(old)==0:pass # already instrumented
        else:raise PreparationError('OBSERVER_PATCH_BASELINE_MISMATCH')
        target.write_text(text,encoding='utf-8',newline='\n');touched.add(path)
    if touched!=HOOK_FILES:raise PreparationError('OBSERVER_PATCH_INCOMPLETE')


def safe_configuration():
    from dotenv import load_dotenv
    from app.configuration import load_configuration
    load_dotenv(dotenv_path=Path.cwd()/'.env',override=False)
    c=load_configuration()
    if c.environment.value!='PAPER' or c.live_trading_enabled or c.grok_paper_entries_enabled:
        raise PreparationError('UNSAFE_RUNTIME_FLAGS')
    return c


def configuration_digest(config):
    import dataclasses,json,hashlib
    def clean(value):
        if dataclasses.is_dataclass(value):value=dataclasses.asdict(value)
        if isinstance(value,dict):
            return {str(k):'REDACTED' if any(s in str(k).lower() for s in
                ('secret','password','token','app_key','api_key','access_key','credential')) else clean(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [clean(v) for v in value]
        if isinstance(value,(set,frozenset)):return sorted((clean(v) for v in value),key=str)
        return value
    return hashlib.sha256(json.dumps(clean(config),sort_keys=True,default=str).encode()).hexdigest()


def compatibility_check():
    safe_configuration()
    from app.composition.desktop import create_desktop_composition
    from app.strategies.warrior_momentum.desktop_sidecar import WarriorDesktopSidecar
    from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
    from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperPaperRuntimeAdapter
    from .grok_observer_context import offline_check
    for target in (create_desktop_composition,WarriorDesktopSidecar,WarriorForwardCaptureService,QuickScalperPaperRuntimeAdapter):
        if 'observer_context_tap' not in inspect.signature(target).parameters:
            raise PreparationError('OBSERVER_HOOK_INTERFACE_MISSING')
    report=offline_check()
    if report['network_requests'] or report['orders']:raise PreparationError('OFFLINE_COMPATIBILITY_FAILED')


def prepare(repo,directory):
    repo,directory=Path(repo).resolve(),Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    summary=dict(scope='INSTALLED_BASELINE_OBSERVER_PREPARATION_V1',ready=False,
        status='PREPARATION_FAILED',atlas_started=False,network_requests=0,grok_requests=0,
        installed_source_modified=False,online_observer_ready=False)
    try:
        summary['stage']='COPY_INSTALLED_SOURCE'
        before=source_manifest(repo)
        frozen=directory/'frozen-source'
        for name in before:
            target=frozen/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(repo/name,target)
        if source_manifest(frozen)!=before or source_manifest(repo)!=before:
            raise PreparationError('SOURCE_CHANGED_DURING_COPY')
        module_root=Path(__file__).resolve().parent
        summary['stage']='APPLY_OBSERVER_HOOKS'
        apply_hooks(frozen,json.loads((module_root/'observer_native_hooks.json').read_text())['patch'])
        for name in OBSERVER_FILES:
            target=frozen/'app/asset_modules'/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(module_root/name,target)
        summary['stage']='COMPARE_EFFECTIVE_CONFIGURATION'
        fingerprint_script=(NETWORK_BLOCK_CODE+inspect.getsource(configuration_digest)+"\n"+"""
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
repo=Path(sys.argv[2]);os.chdir(repo)
from dotenv import load_dotenv
load_dotenv(dotenv_path=repo/'.env',override=False)
from app.configuration import load_configuration
print(configuration_digest(load_configuration()))
""")
        fingerprint_path=directory/'private-configuration-check.py'
        fingerprint_path.write_text(fingerprint_script,encoding='utf-8')
        digests=[]
        for source in (repo,frozen):
            result=subprocess.run([sys.executable,'-I',str(fingerprint_path),str(source),str(repo)],
                                  cwd=repo,capture_output=True,text=True,timeout=30)
            value=result.stdout.strip()
            if result.returncode!=0 or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
                raise PreparationError('CONFIGURATION_COMPARISON_FAILED')
            digests.append(value)
        if digests[0]!=digests[1]:raise PreparationError('EFFECTIVE_CONFIGURATION_MISMATCH')
        receipt=dict(repo=str(repo),source_sha256=_digest(before),staged_sha256=_digest(source_manifest(frozen)),
                     configuration_sha256=digests[0],observer_files=list(OBSERVER_FILES),hook_files=sorted(HOOK_FILES))
        (directory/'private-baseline-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        bootstrap="import os,sys\nif '--check' in sys.argv:\n"+''.join('    '+line+'\n' for line in NETWORK_BLOCK_CODE.splitlines())+"""
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/'frozen-source'))
from app.asset_modules.grok_observer_session import prepared_main
raise SystemExit(prepared_main(root,sys.argv[1:]))
"""
        (directory/'bootstrap-observer.py').write_text(bootstrap,encoding='utf-8')
        summary['stage']='OFFLINE_COMPATIBILITY_CHECK'
        checked=subprocess.run([sys.executable,'-I',str(directory/'bootstrap-observer.py'),'--check'],
                               cwd=repo,capture_output=True,text=True,timeout=60)
        if checked.returncode!=0:raise PreparationError('INSTALLED_BASELINE_COMPATIBILITY_FAILED')
        executable=Path(sys.executable).resolve()
        ps_quote=lambda s:"'"+str(s).replace("'","''")+"'"
        launch=("$ErrorActionPreference = 'Stop'\n& "+ps_quote(executable)+" -I "+
                ps_quote(directory/'bootstrap-observer.py')+" --start\n")
        (directory/'Start-Observer-Capture.ps1').write_text(launch,encoding='utf-8')
        summary.update(ready=True,status='PREPARED_NOT_STARTED',source_files=len(before),source_sha256=receipt['source_sha256'],
                       compatibility_check='OFFLINE_FIXTURES_PASSED',max_seconds=300,max_records=100)
    except Exception as exc:
        summary['status']=str(exc) if isinstance(exc,PreparationError) else 'PREPARATION_FAILED'
        summary['error_type']=type(exc).__name__
    (directory/'preparation-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary


def verify_receipt(directory):
    receipt=json.loads((directory/'private-baseline-receipt.json').read_text())
    repo=Path(receipt['repo'])
    if _digest(source_manifest(repo))!=receipt['source_sha256']:
        raise PreparationError('INSTALLED_BASELINE_CHANGED_REPREPARE')
    if _digest(source_manifest(directory/'frozen-source'))!=receipt['staged_sha256']:
        raise PreparationError('STAGED_SOURCE_CHANGED_REPREPARE')
    return repo,receipt


def _settings(composition):
    return dict(warrior=asdict(composition.warrior_forward_sidecar.strategy_config),
                scalper=asdict(composition.quick_scalper_runtime.config))


class BoundedSession:
    def __init__(self,composition,directory,receipt,*,seconds=300,clock=lambda:datetime.now(UTC),model_block=None):
        from .grok_observer_capture import ContextCapture,MAX_RECORDS
        from .grok_observer_runtime_tap import RuntimeContextTap
        self._composition,self._directory,self._receipt=composition,Path(directory),receipt
        self._initial_settings=_digest(_settings(composition));self._clock=clock
        self._model_block=model_block
        self._lock=Lock();self._stop=Event();self._report=None
        self._started=monotonic();self._deadline=self._started+seconds;self._limit=MAX_RECORDS
        self._started_wall=clock()
        baseline=_digest(dict(source=receipt['source_sha256'],settings=self._initial_settings))
        self._capture=ContextCapture(self._directory/'private-capture',baseline,clock=clock,deadline=self._deadline)
        self._tap=RuntimeContextTap(self._capture,baseline)
        composition.quick_scalper_runtime._observer_context_tap=self._tap
        composition.warrior_forward_sidecar._observer_context_tap=self._tap
        service=composition.warrior_forward_sidecar._service
        if service is not None:service._observer_context_tap=self._tap
        self._thread=Thread(target=self._monitor,name='atlas-bounded-observer-session',daemon=True)
        self._thread.start()

    def _monitor(self):
        while not self._stop.wait(.5):
            with self._capture._lock:
                full=self._capture._counts['enqueued']>=self._limit or self._capture._counts['limit_rejections']>0
                failed=self._capture._failed
            if full or failed or monotonic()>=self._deadline:
                self.close('CAPACITY_REACHED' if full else 'STORAGE_FAILED' if failed else 'TIME_LIMIT')
                return

    def close(self,reason='ATLAS_CLOSED'):
        with self._lock:
            if self._report is not None:return self._report
            self._stop.set();report=self._tap.close()
            stable_settings=_digest(_settings(self._composition))==self._initial_settings
            stable_source=False
            try:stable_source=_digest(source_manifest(Path(self._receipt['repo'])))==self._receipt['source_sha256']
            except Exception:pass
            owners=report['capture']['records_by_engine']
            elapsed=monotonic()-self._started
            finished_wall=self._clock()
            clock_delta=(finished_wall-self._started_wall).total_seconds()-elapsed
            report.update(scope='BOUNDED_NATIVE_EQUITY_CAPTURE_SESSION_V1',stop_reason=reason,
                elapsed_seconds=round(elapsed,3),started_at=self._started_wall.isoformat(),finished_at=finished_wall.isoformat(),
                wall_minus_monotonic_seconds=round(clock_delta,6),clock_discontinuity_observed=abs(clock_delta)>.25,
                settings_unchanged=stable_settings,
                installed_source_unchanged=stable_source,online_observer_ready=False,
                observed_both_engines=all(owners.get(e,0)>0 for e in ('WARRIOR_MOMENTUM_V1','QUICK_SCALPER')),
                status='CAPTURE_FINISHED_REVIEW_REQUIRED',grok_requests=0,live_trading_enabled=False,
                grok_order_review_enabled=False,blocked_model_attempts=0 if self._model_block is None else self._model_block.attempts,
                baseline_sha256=_digest(dict(source=self._receipt['source_sha256'],settings=self._initial_settings)))
            self._report=report
            (self._directory/'session-summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print('UPLOAD_SUMMARY='+str(self._directory/'session-summary.json'),flush=True)
            return report


def run_local_capture(directory,receipt):
    c=safe_configuration()
    if configuration_digest(c)!=receipt['configuration_sha256']:
        raise PreparationError('EFFECTIVE_CONFIGURATION_CHANGED_REPREPARE')
    from app.market.calendar import market_session,MarketSession,trading_day_schedule
    from zoneinfo import ZoneInfo
    now=datetime.now(UTC)
    if market_session(now)!=MarketSession.CORE:raise PreparationError('REGULAR_MARKET_SESSION_REQUIRED')
    schedule=trading_day_schedule(now.astimezone(ZoneInfo('America/New_York')))
    seconds=min(300,(schedule.market_close-now).total_seconds())
    if seconds<5:raise PreparationError('INSUFFICIENT_SESSION_TIME')
    # The user invokes this entrypoint locally. Codex never executes it.
    model_block=ModelRequestBlocker()
    try:
        from app.gui import app as gui_app
    except BaseException:
        model_block.close();raise
    original=gui_app.create_desktop_composition;sessions=[]
    original_dotenv=gui_app.load_dotenv
    gui_app.load_dotenv=lambda override=False:original_dotenv(dotenv_path=Path.cwd()/'.env',override=override)
    def compose(*args,**kwargs):
        safe_configuration()
        composition=original(*args,**kwargs)
        remaining=min(300,(schedule.market_close-datetime.now(UTC)).total_seconds())
        try:
            if remaining<5:raise PreparationError('INSUFFICIENT_SESSION_TIME')
            sessions.append(BoundedSession(composition,directory,receipt,seconds=remaining,model_block=model_block))
        except Exception:
            composition.close();raise
        return composition
    gui_app.create_desktop_composition=compose
    original_argv=sys.argv
    sys.argv=['AtlasObserverCapture']
    try:return gui_app.main()
    finally:
        for session in sessions:session.close()
        gui_app.create_desktop_composition=original
        gui_app.load_dotenv=original_dotenv
        sys.argv=original_argv
        model_block.close()


def prepared_main(directory,argv):
    p=argparse.ArgumentParser();mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check',action='store_true');mode.add_argument('--start',action='store_true')
    args=p.parse_args(argv)
    try:
        if args.start:
            summary_path=directory/'preparation-summary.json'
            if not summary_path.is_file() or json.loads(summary_path.read_text()).get('ready') is not True:
                raise PreparationError('PREPARATION_NOT_READY')
            if (directory/'private-capture').exists():
                raise PreparationError('CAPTURE_ALREADY_USED_REPREPARE')
        repo,receipt=verify_receipt(directory);os.chdir(repo)
        if args.check:compatibility_check();return 0
        return run_local_capture(directory,receipt)
    except Exception as exc:
        status=str(exc) if isinstance(exc,PreparationError) else 'CAPTURE_START_FAILED'
        print('CAPTURE_STATUS='+status)
        return 2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();report=prepare(args.repo,args.output)
    print('CAPTURE_PREPARATION='+report['status']);print('UPLOAD_SUMMARY='+str(args.output/'preparation-summary.json'))
    return 0 if report['ready'] else 2


if __name__=='__main__':raise SystemExit(main())
