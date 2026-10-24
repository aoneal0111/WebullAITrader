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
    return SimpleNamespace(quick_scalper_runtime=SimpleNamespace(config=Policy(),_observer_context_tap=None),
        warrior_forward_sidecar=SimpleNamespace(strategy_config=Policy(),_observer_context_tap=None,_service=None))


def test_bounded_session_closes_only_observer_and_reports_no_fake_samples(tmp_path):
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True);(repo/'app'/'x.py').write_text('x=1')
    receipt=dict(repo=str(repo),source_sha256=session._digest(session.source_manifest(repo)))
    c=composition();runner=session.BoundedSession(c,tmp_path/'output',receipt,seconds=0,clock=lambda:NOW)
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
    monkeypatch.setattr(calendar,'market_session',lambda _:calendar.MarketSession.CLOSED)
    with pytest.raises(session.PreparationError,match='REGULAR_MARKET_SESSION_REQUIRED'):
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


def test_local_gui_entrypoint_uses_stub_and_restores_request_and_factory_hooks(tmp_path,monkeypatch):
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
    monkeypatch.setattr(calendar,'market_session',lambda _:calendar.MarketSession.CORE)
    monkeypatch.setattr(calendar,'trading_day_schedule',lambda _:SimpleNamespace(market_close=session.datetime.now(session.UTC)+timedelta(minutes=10)))
    assert session.run_local_capture(tmp_path/'output',receipt)==0
    assert fake.create_desktop_composition is original and fake.load_dotenv is original_dotenv
    assert GrokClient.request is original_request and calls==[dict(dotenv_path=repo/'.env',override=False)]
    report=json.loads((tmp_path/'output'/'session-summary.json').read_text())
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
