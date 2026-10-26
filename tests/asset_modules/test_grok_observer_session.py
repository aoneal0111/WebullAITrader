from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.asset_modules import grok_observer_session as session
from app.asset_modules.grok_observer_capture import ContextCapture
from tests.asset_modules.test_grok_observer import NOW,BASE,snap
from tests.asset_modules.test_grok_observer_context import evidence


def test_observer_patch_applies_exactly_to_parent_and_is_idempotent(tmp_path):
    import subprocess
    root=Path(__file__).resolve().parents[2]
    patch=json.loads((root/'app/asset_modules/observer_native_hooks.json').read_text())['patch']
    # Repository parent is fixed; no network or workstation source is read.
    for name in session.HOOK_FILES:
        data=subprocess.check_output(['git','show','367169b0628a15beacee2afd59f748787e1d9417:'+name],cwd=root)
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    session.apply_hooks(tmp_path,patch)
    first={name:(tmp_path/name).read_bytes() for name in session.HOOK_FILES}
    session.apply_hooks(tmp_path,patch)
    assert first=={name:(tmp_path/name).read_bytes() for name in session.HOOK_FILES}


def test_patch_refuses_unrelated_or_ambiguous_changes(tmp_path):
    with pytest.raises(session.PreparationError):session.apply_hooks(tmp_path,'diff --git a/secret b/secret\n')
    with pytest.raises(session.PreparationError):session.apply_hooks(tmp_path,'')


def test_source_snapshot_has_bounds_and_excludes_credentials_and_bytecode(tmp_path):
    app=tmp_path/'app';app.mkdir();(app/'a.py').write_text('original')
    (app/'.env').write_text('PRIVATE_SECRET');(app/'a.pyc').write_bytes(b'private')
    manifest=session.source_manifest(tmp_path)
    assert manifest=={'app/a.py':sha256(b'original').hexdigest()}


def test_prepare_preserves_installed_source_and_never_launches_gui(tmp_path,monkeypatch):
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True)
    (repo/'app'/'__init__.py').write_text('ORIGINAL_CODE')
    before=session.source_manifest(repo);calls=[]
    monkeypatch.setattr(session,'apply_hooks',lambda *a:None)
    monkeypatch.setattr(session.subprocess,'run',lambda args,**kw:calls.append(args) or SimpleNamespace(returncode=0,stdout='a'*64))
    report=session.prepare(repo,tmp_path/'output')
    assert report['ready'] and report['status']=='PREPARED_NOT_STARTED'
    assert session.source_manifest(repo)==before and not report['atlas_started']
    assert len(calls)==3 and calls[-1][-1]=='--check' and all('-I' in c for c in calls)
    assert '--start' in (tmp_path/'output'/'Start-Observer-Capture.ps1').read_text()
    (repo/'app'/'__init__.py').write_text('CHANGED')
    with pytest.raises(session.PreparationError,match='BASELINE_CHANGED'):session.verify_receipt(tmp_path/'output')


def test_incompatible_source_is_blocked_without_leaking_exception_text(tmp_path,monkeypatch):
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True);(repo/'app'/'x.py').write_text('x=1')
    monkeypatch.setattr(session,'apply_hooks',lambda *a:None)
    monkeypatch.setattr(session.subprocess,'run',lambda args,**kw:SimpleNamespace(returncode=2 if args[-1]=='--check' else 0,stdout='a'*64))
    report=session.prepare(repo,tmp_path/'output')
    assert not report['ready'] and report['status']=='INSTALLED_BASELINE_COMPATIBILITY_FAILED'
    assert not (tmp_path/'output'/'Start-Observer-Capture.ps1').exists()


def test_capture_deadline_is_checked_before_submission(tmp_path):
    capture=ContextCapture(tmp_path/'capture',BASE,clock=lambda:NOW,deadline=10,timer=lambda:10)
    try:assert not capture.submit(snap(),evidence())
    finally:report=capture.close()
    assert report['counts']['deadline_rejections']==1 and report['counts'].get('written',0)==0


@dataclass(frozen=True)
class Policy:enabled:bool=False


def composition():
    return SimpleNamespace(runtime_service=SimpleNamespace(status='STOPPED'),scanner_marker=None,quick_scalper_runtime=SimpleNamespace(config=Policy(),_observer_context_tap=None),
        warrior_forward_sidecar=SimpleNamespace(strategy_config=Policy(),_observer_context_tap=None,_service=None))


def test_bounded_session_closes_only_observer_and_reports_no_fake_samples(tmp_path):
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True);(repo/'app'/'x.py').write_text('x=1')
    receipt=dict(repo=str(repo),source_sha256=session._digest(session.source_manifest(repo)))
    c=composition();c.runtime_service.status='RUNNING'
    markers=iter((None,'actual-scanner-activity'))
    runner=session.BoundedSession(c,tmp_path/'output',receipt,seconds=0,clock=lambda:NOW,activity_source=lambda:next(markers))
    runner._thread.join(timeout=5)
    report=runner.close()
    assert report['stop_reason']=='TIME_LIMIT' and not report['observed_both_engines']
    assert report['settings_unchanged'] and report['installed_source_unchanged']
    assert report['capture']['writer_stopped'] and report['tap_stopped']
    assert not report['online_observer_ready'] and report['grok_requests']==0


def test_policy_change_is_visible_without_restoring_or_changing_it(tmp_path):
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True);(repo/'app'/'x.py').write_text('x=1')
    receipt=dict(repo=str(repo),source_sha256=session._digest(session.source_manifest(repo)))
    c=composition();runner=session.BoundedSession(c,tmp_path/'output',receipt,clock=lambda:NOW)
    c.quick_scalper_runtime.config=Policy(True)
    report=runner.close()
    assert not report['settings_unchanged'] and c.quick_scalper_runtime.config.enabled is True


def test_closed_session_refuses_before_gui_import_or_launch(tmp_path,monkeypatch):
    from app.market import calendar
    monkeypatch.setattr(session,'safe_configuration',lambda:None)
    monkeypatch.setattr(session,'capture_session_window',lambda _:None)
    with pytest.raises(session.PreparationError,match='EQUITY_MARKET_SESSION_REQUIRED'):
        session.run_local_capture(tmp_path,{'configuration_sha256':session.configuration_digest(None)})


def test_request_blocker_prevents_all_grok_transport_and_restores_method():
    from app.asset_modules.grok import GrokClient,GrokUnavailable
    calls=[];client=GrokClient('fixture-only','grok-4.7',transport=lambda r:calls.append(r),clock=lambda:0)
    original=GrokClient.request;block=session.ModelRequestBlocker()
    try:
        for role in ('WARRIOR_MOMENTUM_V1','QUICK_SCALPER','CRYPTO'):
            with pytest.raises(GrokUnavailable):client.request(role,'fixture',{'mode':'PAPER'},{})
        assert block.attempts==3 and not calls and client.requests==0
    finally:block.close()
    assert GrokClient.request is original


def test_configuration_digest_ignores_credentials_but_detects_policy_changes():
    assert session.configuration_digest(dict(api_key='PRIVATE1',risk=5))==session.configuration_digest(dict(api_key='PRIVATE2',risk=5))
    assert session.configuration_digest(dict(risk=5))!=session.configuration_digest(dict(risk=6))


def test_effective_configuration_change_refuses_before_calendar_or_gui(tmp_path,monkeypatch):
    monkeypatch.setattr(session,'safe_configuration',lambda:dict(risk=6))
    with pytest.raises(session.PreparationError,match='EFFECTIVE_CONFIGURATION_CHANGED'):
        session.run_local_capture(tmp_path,dict(configuration_sha256=session.configuration_digest(dict(risk=5))))


def test_prepared_source_tampering_refuses_start(tmp_path):
    repo=tmp_path/'repo';stage=tmp_path/'frozen-source'
    for root in (repo,stage):
        (root/'app').mkdir(parents=True);(root/'app'/'x.py').write_text('x=1')
    receipt=dict(repo=str(repo),source_sha256=session._digest(session.source_manifest(repo)),
        staged_sha256=session._digest(session.source_manifest(stage)))
    (tmp_path/'private-baseline-receipt.json').write_text(json.dumps(receipt))
    (stage/'app'/'x.py').write_text('x=2')
    with pytest.raises(session.PreparationError,match='STAGED_SOURCE_CHANGED'):
        session.verify_receipt(tmp_path)


@pytest.mark.parametrize('session_name',['PREMARKET','CORE','AFTER_HOURS'])
def test_local_gui_entrypoint_uses_stub_and_restores_request_and_factory_hooks(tmp_path,monkeypatch,session_name):
    import sys
    from types import ModuleType
    from app.market import calendar
    from app.asset_modules.grok import GrokClient
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True);(repo/'app'/'x.py').write_text('x=1')
    monkeypatch.chdir(repo)
    receipt=dict(repo=str(repo),source_sha256=session._digest(session.source_manifest(repo)),
                 configuration_sha256=session.configuration_digest(None))
    c=composition();calls=[]
    fake=SimpleNamespace(create_desktop_composition=lambda *a,**k:c,load_dotenv=lambda **k:calls.append(k))
    original=fake.create_desktop_composition;original_dotenv=fake.load_dotenv;original_request=GrokClient.request
    def main():
        fake.load_dotenv(override=False)
        assert fake.create_desktop_composition() is c
        return 0
    fake.main=main;package=ModuleType('app.gui');package.app=fake
    monkeypatch.setitem(sys.modules,'app.gui',package)
    monkeypatch.setattr(session,'safe_configuration',lambda:None)
    monkeypatch.setattr(session,'capture_session_window',lambda _:(session_name,session.datetime.now(session.UTC)+timedelta(minutes=10)))
    assert session.run_local_capture(tmp_path/'output',receipt)==0
    assert fake.create_desktop_composition is original and fake.load_dotenv is original_dotenv
    assert GrokClient.request is original_request and calls==[dict(dotenv_path=repo/'.env',override=False)]
    report=json.loads((tmp_path/'output'/'session-summary.json').read_text())
    assert report['capture_session_at_start']==session_name
    assert report['stop_reason']=='ATLAS_CLOSED' and report['tap_stopped'] and report['grok_requests']==0


def test_failed_preparation_cannot_be_started_via_bootstrap(tmp_path,monkeypatch):
    monkeypatch.setattr(session,'verify_receipt',lambda *a:pytest.fail('must stop before source checks or GUI'))
    (tmp_path/'preparation-summary.json').write_text(json.dumps(dict(ready=False)))
    assert session.prepared_main(tmp_path,['--start'])==2


def test_capture_is_one_shot_without_launching_a_second_gui(tmp_path,monkeypatch):
    monkeypatch.setattr(session,'verify_receipt',lambda *a:pytest.fail('must stop before source checks or GUI'))
    (tmp_path/'preparation-summary.json').write_text(json.dumps(dict(ready=True)))
    (tmp_path/'private-capture').mkdir()
    assert session.prepared_main(tmp_path,['--start'])==2


@pytest.fixture
def timed_session(tmp_path):
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True);(repo/'app'/'x.py').write_text('x=1')
    receipt=dict(repo=str(repo),source_sha256=session._digest(session.source_manifest(repo)))
    timer=[0.0];c=composition()
    runner=session.BoundedSession(c,tmp_path/'output',receipt,clock=lambda:NOW+timedelta(seconds=timer[0]),
        timer=lambda:timer[0],start_monitor=False,activity_source=lambda:c.scanner_marker)
    try:yield runner,c,timer
    finally:runner.close()


def test_six_minute_gui_delay_preserves_full_capture_and_deadline(timed_session):
    from dataclasses import replace
    runner,c,timer=timed_session
    timer[0]=360
    assert runner._poll() is None and runner._observation_started is None
    # Startup has elapsed beyond the old five-minute timeout, but cannot write.
    assert not runner._capture.submit(snap(),evidence())
    c.runtime_service.status='STARTING'
    assert runner._poll() is None and runner._observation_started is None
    c.runtime_service.status='RUNNING';c.scanner_marker='first-evaluation'
    assert runner._poll() is None and runner._deadline==660
    baseline=runner._capture._baseline
    s=replace(snap(),baseline_sha256=baseline,bid_at=NOW+timedelta(seconds=360),
        ask_at=NOW+timedelta(seconds=360),captured_at=NOW+timedelta(seconds=360))
    # Encoding itself is already covered by real context tests. Here use the
    # actual writer with valid evidence and the active clock/deadline.
    e=replace(evidence(s),baseline_sha256=baseline,captured_at=s.captured_at,
        bars=tuple(replace(b,closed_at=b.closed_at+timedelta(seconds=360),
            available_at=b.available_at+timedelta(seconds=360)) for b in evidence(s).bars))
    assert runner._capture.submit(s,e)
    timer[0]=659
    assert runner._poll() is None
    # Stopping/restarting the runtime does not extend the observation budget.
    c.runtime_service.status='STOPPED';assert runner._poll() is None
    c.runtime_service.status='RUNNING';timer[0]=660
    assert runner._poll()=='TIME_LIMIT'
    assert not runner._capture.submit(s,e)
    r=runner.close('TIME_LIMIT')
    assert r['startup_wait_seconds']==360 and r['observation_elapsed_seconds']==300
    assert r['capture']['counts']['written']==1 and not r['observed_both_engines']
    assert r['evidence_status']=='PARTIAL_ENGINE_RECORDS'
    assert r['settings_unchanged'] and r['installed_source_unchanged']


def test_no_start_is_bounded_and_empty_capture_is_explicit(timed_session):
    runner,c,timer=timed_session
    timer[0]=899;assert runner._poll() is None
    timer[0]=900;assert runner._poll()=='STARTUP_TIMEOUT'
    r=runner.close('STARTUP_TIMEOUT')
    assert r['observation_started_at'] is None and r['observation_elapsed_seconds']==0
    assert r['evidence_status']=='NO_ENGINE_RECORDS' and not r['observed_both_engines']
    assert c.runtime_service.status=='STOPPED'


def test_start_at_zero_is_not_confused_with_no_start(timed_session):
    runner,c,timer=timed_session
    c.runtime_service.status='RUNNING';c.scanner_marker='first-evaluation';assert runner._poll() is None
    timer[0]=300;assert runner._poll()=='TIME_LIMIT'
    r=runner.close('TIME_LIMIT');assert r['startup_wait_seconds']==0
    assert r['observation_elapsed_seconds']==300


def test_market_close_caps_wait_and_active_capture(tmp_path):
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True);(repo/'app'/'x.py').write_text('x=1')
    receipt=dict(repo=str(repo),source_sha256=session._digest(session.source_manifest(repo)))
    for started in (False,True):
        timer=[0.0];c=composition()
        runner=session.BoundedSession(c,tmp_path/str(started),receipt,clock=lambda:NOW+timedelta(seconds=timer[0]),
            timer=lambda:timer[0],session_end=NOW+timedelta(seconds=120),start_monitor=False,activity_source=lambda:c.scanner_marker)
        try:
            if started:
                timer[0]=100;c.runtime_service.status='RUNNING';c.scanner_marker='first-evaluation'
                assert runner._poll() is None and runner._deadline==120
            timer[0]=120;assert runner._poll()=='SESSION_END'
            assert runner.close('SESSION_END')['observation_elapsed_seconds']==(20 if started else 0)
        finally:runner.close()



def test_storage_failure_while_waiting_stops_observer_without_starting_runtime(timed_session):
    runner,c,timer=timed_session
    with runner._capture._lock:runner._capture._failed=True
    assert runner._poll()=='STORAGE_FAILED'
    assert runner.close('STORAGE_FAILED')['observation_started_at'] is None
    assert c.runtime_service.status=='STOPPED'



def test_background_running_does_not_start_capture_until_scanner_evaluates(timed_session):
    runner,c,timer=timed_session
    c.runtime_service.status='RUNNING'
    timer[0]=9.119
    assert runner._poll() is None and runner._observation_started is None
    timer[0]=300
    assert runner._poll() is None and runner._observation_started is None
    timer[0]=300.767;c.scanner_marker='new-scanner-evaluation'
    assert runner._poll() is None
    assert runner._deadline==600.767
    timer[0]=600.766;assert runner._poll() is None
    timer[0]=600.767;assert runner._poll()=='TIME_LIMIT'


def test_prior_scanner_marker_cannot_start_new_capture(tmp_path):
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True);(repo/'app'/'x.py').write_text('x=1')
    receipt=dict(repo=str(repo),source_sha256=session._digest(session.source_manifest(repo)))
    timer=[0.0];marker=['old-run'];c=composition()
    runner=session.BoundedSession(c,tmp_path/'out',receipt,clock=lambda:NOW,timer=lambda:timer[0],
        start_monitor=False,activity_source=lambda:marker[0])
    try:
        timer[0]=300;assert runner._poll() is None and runner._observation_started is None
        marker[0]='new-run';assert runner._poll() is None and runner._deadline==600
    finally:runner.close()



def test_default_activity_source_uses_real_scanner_diagnostic_not_runtime_state(tmp_path,monkeypatch):
    from app import performance_diagnostics as module
    diag=module.PerformanceDiagnostics()
    monkeypatch.setattr(module,'performance_diagnostics',diag)
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True);(repo/'app'/'x.py').write_text('x=1')
    receipt=dict(repo=str(repo),source_sha256=session._digest(session.source_manifest(repo)))
    timer=[0.0];c=composition();c.runtime_service.status='RUNNING'
    runner=session.BoundedSession(c,tmp_path/'out',receipt,clock=lambda:NOW,
        timer=lambda:timer[0],start_monitor=False)
    try:
        diag.record_startup_stage('runtime_started')
        timer[0]=300;assert runner._poll() is None and runner._observation_started is None
        diag.record_startup_stage('first_scanner_evaluation')
        assert runner._poll() is None and runner._deadline==600
        assert runner.close()['capture_timer_start']=='SCANNER_FIRST_EVALUATION'
    finally:runner.close()



def test_entry_counter_export_is_redacted_and_delta_is_reset_aware(monkeypatch):
    from app import performance_diagnostics as module
    diag=module.PerformanceDiagnostics()
    monkeypatch.setattr(module,'performance_diagnostics',diag)
    before=session._entry_diagnostic_counts()
    diag.increment('quick_scalper_assessments',3)
    diag.increment('quick_scalper_initial_insufficient_edge',2)
    diag.record_entry_funnel('PRIVATE_SYMBOL','EXECUTION_QUOTE_REQUESTED')
    delta=session._entry_diagnostic_delta(before,session._entry_diagnostic_counts())
    assert delta['counts']['quick_scalper_assessments']==3
    assert delta['counts']['quick_scalper_initial_insufficient_edge']==2
    assert delta['counts']['equity_funnel_EXECUTION_QUOTE_REQUESTED']==1
    assert 'PRIVATE_SYMBOL' not in str(delta)
    assert not session._entry_diagnostic_delta({'x':2},{'x':1})['counts']
    assert session._entry_diagnostic_delta({'x':2},{'x':1})['counter_reset_observed']


def test_session_reports_disabled_scalper_without_enabling_it(timed_session):
    runner,c,timer=timed_session
    report=runner.close()
    assert report['scalper_readiness']['configured_enabled'] is False
    assert c.quick_scalper_runtime.config.enabled is False
    assert 'entry_diagnostics' in report and report['grok_requests']==0


@pytest.mark.parametrize('date,hour,minute,expected,end_hour',[
    ('2026-10-26',3,59,None,None),
    ('2026-10-26',4,0,'PREMARKET',20),
    ('2026-10-26',9,29,'PREMARKET',20),
    ('2026-10-26',9,30,'CORE',20),
    ('2026-10-26',15,59,'CORE',20),
    ('2026-10-26',16,0,'AFTER_HOURS',20),
    ('2026-10-26',19,59,'AFTER_HOURS',20),
    ('2026-10-26',20,0,None,None),
    ('2026-10-25',10,0,None,None),
    ('2026-11-26',10,0,None,None),
    ('2026-11-27',12,59,'CORE',17),
    ('2026-11-27',13,0,'AFTER_HOURS',17),
    ('2026-11-27',16,59,'AFTER_HOURS',17),
    ('2026-11-27',17,0,None,None),
    ('2026-11-02',4,0,'PREMARKET',20),
], ids=['overnight-before','premarket-open','before-core','core-open','before-close',
        'after-hours-open','before-extended-close','extended-close','weekend','holiday',
        'before-early-close','early-after-hours','before-early-extended-close',
        'early-extended-close','after-dst-change'])
def test_capture_window_supports_extended_hours_calendar_and_dst(date,hour,minute,expected,end_hour):
    from datetime import datetime,UTC
    from zoneinfo import ZoneInfo
    local=datetime.fromisoformat(date).replace(hour=hour,minute=minute,tzinfo=ZoneInfo('America/New_York'))
    result=session.capture_session_window(local.astimezone(UTC))
    if expected is None:assert result is None
    else:
        assert result[0]==expected
        assert result[1].astimezone(ZoneInfo('America/New_York')).hour==end_hour
        assert result[1].date()==local.date()


def test_capture_window_rejects_naive_time():
    from datetime import datetime
    with pytest.raises(ValueError,match='Aware'):session.capture_session_window(datetime(2026,10,26,16))
