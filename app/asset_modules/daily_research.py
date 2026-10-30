"""Opt-in recording on the normal visible Atlas desktop; never enables entries."""
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

from .daily_research_journal import DailyJournal, canonical, safe as safe_settings
from .grok_observer_capture import ContextCapture
from .grok_observer_runtime_tap import RuntimeContextTap


class DailyResearch:
    def __init__(self,configuration,*,root=None,clock=lambda:datetime.now(UTC),seconds=16*3600):
        from .grok_execution_trial import configured as configured_execution
        self.grok_execution=configured_execution(configuration,clock=clock)
        if (configuration.environment.value!='PAPER' or configuration.live_trading_enabled
                or (configuration.grok_paper_entries_enabled and self.grok_execution is None)):raise ValueError('DAILY_RESEARCH_REQUIRES_PAPER_OBSERVATION_FLAGS')
        if not 1<=seconds<=16*3600:raise ValueError('BOUNDED_DAILY_DURATION_REQUIRED')
        from .grok_observer_session import configuration_digest,source_manifest
        source=source_manifest(Path(__file__).resolve().parents[2])
        source_hash=sha256(canonical(source)).hexdigest()
        config_hash=configuration_digest(configuration)
        from app.equity_overhaul.paper_experiment import ENV, load
        experiment_path=os.environ.get(ENV,'').strip()
        experiment=load(experiment_path)[1] if experiment_path else None
        baseline_material=dict(source=source_hash,configuration=config_hash)
        if experiment:baseline_material['paper_experiment']=experiment
        self.baseline=sha256(canonical(baseline_material)).hexdigest()
        root=Path(root) if root is not None else Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'AtlasResearch'
        self.directory=root/('daily-research-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex)
        self.directory.mkdir(parents=True,exist_ok=False)
        try:
            self.journal=DailyJournal(self.directory/'journal',self.baseline,clock=clock,
                **({'max_bytes':4*1024*1024*1024,'max_records':8_000_000} if experiment else {}))
            if experiment:
                pointer=Path(experiment_path).with_name('recording-session.json')
                pointer.write_text(json.dumps({'session':str(self.directory),'trial_id':experiment['trial_id']}),encoding='utf-8')
            self.capture=ContextCapture(self.directory/'private-capture',self.baseline,clock=clock,
                max_records=10000,max_bytes=64*1024*1024)
            self.tap=RuntimeContextTap(self.capture,self.baseline,journal=self.journal)
            self._stop=Event();self._closed=False
            self._composition=None;self._settings_hash=None;self._policy_changed=False
            from .grok_observer_session import _entry_diagnostic_counts
            self._entry_counts_before=_entry_diagnostic_counts()
            self._last_active_readiness=None
            self._deadline=monotonic()+seconds
            manifest=dict(scope='NORMAL_DESKTOP_DAILY_RESEARCH_V1',paper_experiment=experiment,baseline_sha256=self.baseline,
                source_sha256=source_hash,configuration_sha256=config_hash,source_files=source,
                started_at=clock().isoformat(),max_seconds=seconds,live_trading_enabled=False,
                grok_order_review_enabled=bool(self.grok_execution),automatic_grok_requests=0,
                grok_execution_trial=None if self.grok_execution is None else self.grok_execution.doc,
                automatic_grok_requests_on_operator_start=bool(self.grok_execution),
                strategy_baseline_changed=False,entries_enabled_by_recorder=False,
                coverage=['Subscribed native market events','Paper order and fill runtime events',
                    'Observed bar windows','Warrior and Scalper quote assessments',
                    'Scalper stream assessments when existing hook is reached'],
                limitations=['No unobserved symbols, pre-start ticks, options chains or news invented',
                    'Fresh context sample limited to 10000 records/64MiB; raw journal continues if context fills',
                    f'Raw journal bounded to {self.journal.max_bytes} bytes/{self.journal.max_records} records; queue drops invalidate complete replay',
                    'Recording grants no entries; native admission or an explicit bounded PAPER experiment controls buys'])
            (self.directory/'research-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
            self._thread=Thread(target=self._monitor,name='atlas-daily-recording-lifecycle',daemon=True)
            self._thread.start()
            print('DAILY_RESEARCH_DIRECTORY='+str(self.directory),flush=True)
        except Exception:
            for name in ('tap','capture','journal'):
                resource=getattr(self,name,None)
                if resource is not None:
                    try:resource.close()
                    except Exception:pass
            raise

    def bind_baseline(self,composition):
        from .grok_observer_session import _settings
        self._composition=composition
        settings=_settings(composition)
        self._settings_hash=sha256(canonical(safe_settings(settings))).hexdigest()
        self.journal.submit('NATIVE_POLICY_BASELINE',dict(settings=settings,settings_sha256=self._settings_hash))
        self._write_readiness()

    def _check_policy(self):
        if self._composition is None:return
        from .grok_observer_session import _settings
        current=sha256(canonical(safe_settings(_settings(self._composition)))).hexdigest()
        if current!=self._settings_hash and not self._policy_changed:
            self._policy_changed=True
            self.journal.submit('NATIVE_POLICY_CHANGED',dict(initial_sha256=self._settings_hash,current_sha256=current))

    def _write_readiness(self):
        composition=self._composition
        warrior=getattr(composition,'warrior_forward_sidecar',None)
        scalper=getattr(composition,'quick_scalper_runtime',None)
        service=getattr(warrior,'_service',None)
        with self.tap._lock:counts=dict(self.tap._counts)
        report=dict(scope='DAILY_NATIVE_ENGINE_READINESS_V1',updated_at=datetime.now(UTC).isoformat(),
            warrior_enabled=getattr(warrior,'enabled',None),
            warrior_service_running=service is not None,
            warrior_scanner_bound=getattr(warrior,'_adapter',None) is not None,
            warrior_health=str(getattr(warrior,'_health','UNKNOWN')),
            warrior_tap_bound=getattr(warrior,'_observer_context_tap',None) is self.tap,
            service_tap_bound=service is not None and getattr(service,'_observer_context_tap',None) is self.tap,
            scalper_enabled=getattr(scalper,'enabled',None),
            scalper_tap_bound=getattr(scalper,'_observer_context_tap',None) is self.tap,
            tap_counts=counts,engine_evidence_seen=counts.get('bars_queued',0)>0 or counts.get('assessments_queued',0)>0,
            orders_dispatched=0,grok_requests=0)
        if service is not None:self._last_active_readiness=report
        target=self.directory/'engine-readiness.json';temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report,indent=2),encoding='utf-8');temporary.replace(target)
        return report

    def _monitor(self):
        while not self._stop.wait(2):
            try:self._check_policy()
            except Exception:
                self.journal.submit('POLICY_CHECK_FAILED',{})
                self._policy_changed=True
            try:self._write_readiness()
            except Exception:self.journal.submit('READINESS_CHECK_FAILED',{})
            if monotonic()>=self._deadline:
                self.close('TIME_LIMIT');return

    def close(self,reason='ATLAS_CLOSED'):
        # Called after normal runtime shutdown; this never stops Atlas or sells.
        if self._closed:return True
        self._closed=True;self._stop.set()
        try:self._check_policy()
        except Exception:
            self._policy_changed=True
            self.journal.submit('POLICY_CHECK_FAILED',{})
        from .grok_observer_session import _entry_diagnostic_counts,_entry_diagnostic_delta
        entry_diagnostics=_entry_diagnostic_delta(self._entry_counts_before,_entry_diagnostic_counts())
        capture=self.tap.close();journal=self.journal.close()
        context_path=self.directory/'private-capture/private-observer-context.jsonl'
        context_sha=sha256(context_path.read_bytes()).hexdigest() if context_path.exists() else None
        report=dict(context_sha256=context_sha,scope='NORMAL_DESKTOP_DAILY_RESEARCH_SUMMARY_V1',reason=reason,
            baseline_sha256=self.baseline,journal=journal,context_capture=capture,
            policy_unchanged=not self._policy_changed,entry_diagnostics=entry_diagnostics,
            last_active_engine_readiness=self._last_active_readiness,
            engine_evidence_status=('ENGINE_EVIDENCE_RECORDED' if capture['tap_counts'].get('bars_queued',0)>0
                or capture['tap_counts'].get('assessments_queued',0)>0 else 'NO_ENGINE_EVIDENCE_REVIEW_REQUIRED'),
            grok_execution_status=None if self.grok_execution is None else self.grok_execution.status(),
            orders_dispatched=0,grok_requests=0,execution_authorized=False)
        (self.directory/'research-summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return journal.get('writer_stopped',False) and capture.get('tap_stopped',False)


def configured_research(configuration,runtime_mode,existing_tap=None):
    if not getattr(configuration,'daily_research_enabled',False):return None
    if runtime_mode.value!='PAPER' or existing_tap is not None:
        raise ValueError('DAILY_RECORDING_REQUIRES_NORMAL_PAPER_DESKTOP_WITHOUT_SECOND_CAPTURE')
    try:return DailyResearch(configuration)
    except Exception:
        import logging
        logging.getLogger(__name__).error('Daily research recording unavailable; native strategy and protection unchanged')
        return None
