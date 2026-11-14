import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from app.strategy_lab.crypto_workspace_control import CryptoWorkspaceControl
from app.strategy_lab.crypto_workspace_upgrade import patch_engine


class Reader:
    def __init__(self,work):self.work=work
    def discover(self):return (self.work,)


def controller(tmp_path):
    work=tmp_path/'paper';work.mkdir()
    (work/'config.json').write_text(json.dumps({'dry_run':True}))
    (work/'profile.json').write_text(json.dumps({'live_trading_enabled':False,'key':'boll','fleet_id':'a'*32}))
    calls=[];state={'running':True}
    def run(command,**kwargs):
        calls.append(command)
        if command[1]=='inspect':
            return NS(returncode=0,stdout=json.dumps(dict(running=state['running'],mounts=[
                dict(Type='bind',Destination='/work',Source=str(work))])))
        assert command[1] in ('start','stop')
        assert command[-1]=='atlas-bollinger-paper-'+'a'*32
        state['running']=command[1]=='start'
        return NS(returncode=0,stdout='')
    return work,CryptoWorkspaceControl(Reader(work),run=run,docker='docker'),calls,state


def test_stop_verifies_container_and_start_resumes_same_wallet(tmp_path):
    work,c,calls,state=controller(tmp_path)
    assert c.execute('stop',work)['success']
    assert (work/'PAUSE_NEW_ENTRIES').is_file() and not state['running']
    assert c.execute('start',work)['success'] and state['running']
    assert not (work/'PAUSE_NEW_ENTRIES').exists()
    assert all('rm' not in command and 'kill' not in command for command in calls)


def test_wrong_wallet_or_nonpaper_refuses_before_any_command(tmp_path):
    work,c,calls,state=controller(tmp_path)
    assert not c.execute('stop',tmp_path/'other')['success'] and not calls
    (work/'config.json').write_text('{"dry_run": false}')
    assert not c.execute('start',work)['success'] and not calls


def test_stop_error_and_container_still_running_never_report_success(tmp_path):
    work,c,calls,state=controller(tmp_path)
    original=c.run
    def run(cmd,**kw):
        if cmd[1]=='stop':return NS(returncode=1,stdout='')
        return original(cmd,**kw)
    c.run=run
    assert not c.execute('stop',work)['success'] and state['running']


def test_wrong_mount_rejected(tmp_path):
    work,c,calls,state=controller(tmp_path)
    c.run=lambda *a,**kw:NS(returncode=0,stdout='{"running":true,"mounts":[]}')
    assert not c.execute('stop',work)['success']
    assert not (work/'PAUSE_NEW_ENTRIES').exists()


def test_emergency_targets_discovered_crypto_only_and_retains_holdings(tmp_path):
    work,c,calls,state=controller(tmp_path)
    result=c.execute('emergency')
    assert result['success'] and result['positions_sold'] is False
    assert any(cmd[1:4]==['stop','-t','10'] for cmd in calls)


def test_overlay_is_idempotent_and_refuses_unrecognized_source():
    raw='                now=datetime.now(UTC);evidence.capture(bot,now)\n'
    patched=patch_engine(raw,'crypto_fleet_runtime.py')
    assert patch_engine(patched,'crypto_fleet_runtime.py')==patched
    assert patched.startswith(raw) and 'publish(work,bot,evidence,now)' in patched
    with pytest.raises(ValueError):patch_engine('different source','crypto_fleet_runtime.py')

@pytest.mark.parametrize('running,paused',[(True,False),(True,True),(False,True),(False,False)])
def test_overlay_apply_preserves_previous_worker_state_wallet_config_and_pause(tmp_path,monkeypatch,running,paused):
    from hashlib import sha256
    from app.strategy_lab import crypto_workspace_upgrade as upgrade
    work,c,calls,state=controller(tmp_path);state['running']=running
    original=(work/'config.json').read_bytes()
    if paused:(work/'PAUSE_NEW_ENTRIES').write_bytes(b'PREVIOUS_PAUSE')
    output=tmp_path/'overlay';output.mkdir()
    plan=[dict(work=str(work),container='atlas-bollinger-paper-'+'a'*32,files=['original.json'],
        overlay='overlay.yaml',service='boll',mount_layout='EXISTING_MODULE_DIRECTORY_V2',config_hashes={'config.json':sha256(original).hexdigest()})]
    (output/'plan.json').write_text(json.dumps(plan))
    monkeypatch.setattr(upgrade,'CryptoWorkspaceControl',lambda:c)
    def run(command,**kw):
        calls.append(command)
        if command[1]=='start':state['running']=True
        assert 'rm' not in command
        return NS(returncode=0)
    monkeypatch.setattr(upgrade.subprocess,'run',run)
    monkeypatch.setattr(upgrade,'verify_overlay',lambda *args:None)
    upgrade.apply(output)
    assert state['running']==running and (work/'config.json').read_bytes()==original
    assert (work/'PAUSE_NEW_ENTRIES').exists()==paused
    if paused:assert (work/'PAUSE_NEW_ENTRIES').read_bytes()==b'PREVIOUS_PAUSE'
    assert any('--no-start' in cmd and '--no-deps' in cmd for cmd in calls)


def test_docker_desktop_path_aliases_do_not_break_wallet_identity():
    from app.strategy_lab.crypto_workspace_control import same_host_path
    assert same_host_path('/run/desktop/mnt/host/c/Users/aonea/paper',r'C:\Users\aonea\paper')
    assert same_host_path('/host_mnt/c/Users/aonea/paper',r'C:\Users\aonea\paper')
    assert not same_host_path('/host_mnt/c/Users/aonea/other',r'C:\Users\aonea\paper')


def setup_prepare(tmp_path,monkeypatch,*,recover=False):
    from app.strategy_lab import crypto_workspace_upgrade as upgrade
    work,c,calls,state=controller(tmp_path)
    source=tmp_path/'original';folder=source/'app/strategy_lab';folder.mkdir(parents=True)
    original={}
    for name in ('nfi_forward_engine.py','crypto_fleet_runtime.py','strategy-rules.json'):
        original[name]=Path(upgrade.__file__).with_name(name).read_text() if name.endswith('.py') else '{"unchanged":true}'
        (folder/name).write_text(original[name])
    compose=tmp_path/'compose.json';compose.write_text('{}')
    reader=c.reader
    reader.read=lambda:dict(engines=[dict(work=str(work),process_liveness_verified=True)])
    monkeypatch.setattr(upgrade,'CryptoMonitorReader',lambda base:reader)
    monkeypatch.setattr(upgrade,'CryptoWorkspaceControl',lambda reader:c)
    def run(command,**kwargs):
        return NS(returncode=0,stdout=json.dumps(dict(mounts=[dict(Type='bind',Source=str(source),
            Destination='/check',RW=False)],files=str(tmp_path/'failed-overlay.json') if recover else str(compose),service='boll')))
    monkeypatch.setattr(upgrade.subprocess,'run',run)
    monkeypatch.setattr(upgrade,'rendered',lambda *a:dict(services=dict(boll=dict(volumes=[
        dict(type='bind',source=str(source),target='/check',read_only=True),
        dict(type='bind',source=str(work),target='/work')]))))
    monkeypatch.setattr(upgrade,'verify_overlay',lambda *args:None)
    return upgrade,work,folder,original,compose


def test_prepare_mounts_existing_directory_and_preserves_original_sources(tmp_path,monkeypatch):
    upgrade,work,folder,original,compose=setup_prepare(tmp_path,monkeypatch)
    assert not (folder/'crypto_workspace_telemetry.py').exists()
    plan=upgrade.prepare(tmp_path/'output')
    for name,raw in original.items():assert (folder/name).read_text()==raw
    overlay=Path(plan[0]['overlay']).read_text()
    assert 'volumes: !override ' in overlay
    volumes=json.loads(overlay.split('volumes: !override ',1)[1])
    within=[v for v in volumes if upgrade.inside_module(v['target'])]
    assert len(within)==1 and within[0]['target']=='/check/app/strategy_lab' and within[0]['read_only']
    bundle=Path(within[0]['source'])
    assert (bundle/'crypto_workspace_telemetry.py').is_file()
    assert (bundle/'strategy-rules.json').read_text()==original['strategy-rules.json']
    assert plan[0]['mount_layout']=='EXISTING_MODULE_DIRECTORY_V2'
    assert (work/'config.json').read_text()=='{"dry_run": true}'


def test_recovery_preparation_uses_saved_original_files_and_running_state(tmp_path,monkeypatch):
    from hashlib import sha256
    upgrade,work,folder,original,compose=setup_prepare(tmp_path,monkeypatch,recover=True)
    prior=tmp_path/'prior-plan.json'
    prior.write_text(json.dumps([dict(work=str(work),container='atlas-bollinger-paper-'+'a'*32,
        was_running=True,files=[str(compose)],service='boll',config_hashes={
            'config.json':sha256((work/'config.json').read_bytes()).hexdigest()})]))
    plan=upgrade.prepare(tmp_path/'output',recover_plan=prior)
    assert plan[0]['files']==[str(compose)] and plan[0]['restore_running'] is True


def test_compose_verification_rejects_nested_file_mounts_and_policy_changes(tmp_path,monkeypatch):
    from copy import deepcopy
    from hashlib import sha256
    from app.strategy_lab import crypto_workspace_upgrade as upgrade
    bundle=tmp_path/'bundle';bundle.mkdir();helper=bundle/'crypto_workspace_telemetry.py';helper.write_text('# helper')
    overlay=tmp_path/'overlay.yaml';overlay.write_text('unused by mocked renderer')
    (tmp_path/'overlay-manifest.json').write_text(json.dumps(dict(module_source=str(bundle),
        bundle_hashes={'crypto_workspace_telemetry.py':sha256(helper.read_bytes()).hexdigest()})))
    mount=dict(type='bind',source=str(bundle),target=upgrade.MODULE_TARGET,read_only=True)
    wallet=dict(type='bind',source='wallet',target='/work')
    before=dict(name='same-project',services=dict(boll=dict(image='pinned',command=['same','args'],
        volumes=[wallet,dict(type='bind',source='old.py',target=upgrade.MODULE_TARGET+'/old.py')],environment={'LIVE':'false'})))
    after=deepcopy(before);after['services']['boll']['volumes']=[deepcopy(wallet),mount]
    def check(updated):
        monkeypatch.setattr(upgrade,'rendered',lambda docker,files:deepcopy(updated if str(overlay) in files else before))
        upgrade.verify_overlay('docker',['original.json'],overlay,'boll')
    check(after)
    changed=deepcopy(after);changed['services']['boll']['command']=['different']
    with pytest.raises(ValueError,match='NON_TELEMETRY'):check(changed)
    changed=deepcopy(after);changed['services']['boll']['volumes'][0]['source']='other-wallet'
    with pytest.raises(ValueError,match='NON_TELEMETRY'):check(changed)
    changed=deepcopy(after);changed['services']['boll']['volumes'].append(dict(type='bind',source='bad.py',target=upgrade.MODULE_TARGET+'/new.py'))
    with pytest.raises(ValueError,match='NESTED_MODULE_FILE'):check(changed)
    helper.write_text('# tampered')
    with pytest.raises(ValueError,match='BUNDLE_CHANGED'):check(after)


def recovery_plan(tmp_path,work):
    from hashlib import sha256
    item=dict(work=str(work),container='atlas-bollinger-paper-'+'a'*32,files=['original.json'],
        overlay='new-overlay.yaml',service='boll',mount_layout='EXISTING_MODULE_DIRECTORY_V2',
        was_running=True,restore_running=True,config_hashes={'config.json':sha256((work/'config.json').read_bytes()).hexdigest()})
    output=tmp_path/'plan';output.mkdir();(output/'plan.json').write_text(json.dumps([item]))
    return output


def test_failed_start_rolls_back_original_compose_and_recovers_running_worker(tmp_path,monkeypatch):
    import subprocess
    from app.strategy_lab import crypto_workspace_upgrade as upgrade
    work,c,calls,state=controller(tmp_path);state['running']=False
    (work/'PAUSE_NEW_ENTRIES').write_bytes(b'ORIGINAL_PAUSE')
    output=recovery_plan(tmp_path,work)
    monkeypatch.setattr(upgrade,'CryptoWorkspaceControl',lambda:c)
    monkeypatch.setattr(upgrade,'verify_overlay',lambda *a:None)
    starts=[]
    def run(command,**kwargs):
        calls.append(command)
        if command[1]=='start':
            starts.append(command)
            if len(starts)==1:raise subprocess.CalledProcessError(1,command)
            state['running']=True
        return NS(returncode=0)
    monkeypatch.setattr(upgrade.subprocess,'run',run)
    with pytest.raises(subprocess.CalledProcessError):upgrade.apply(output)
    assert state['running'] and len(starts)==2
    creates=[cmd for cmd in calls if 'up' in cmd]
    assert 'new-overlay.yaml' in creates[0] and 'new-overlay.yaml' not in creates[1]
    assert (work/'PAUSE_NEW_ENTRIES').read_bytes()==b'ORIGINAL_PAUSE'


def test_restore_original_recovers_stopped_worker_without_new_overlay(tmp_path,monkeypatch):
    from app.strategy_lab import crypto_workspace_upgrade as upgrade
    work,c,calls,state=controller(tmp_path);state['running']=False
    output=recovery_plan(tmp_path,work)
    monkeypatch.setattr(upgrade,'CryptoWorkspaceControl',lambda:c)
    monkeypatch.setattr(upgrade,'rendered',lambda *a:{})
    def run(command,**kwargs):
        calls.append(command)
        assert 'new-overlay.yaml' not in command
        if command[1]=='start':state['running']=True
        return NS(returncode=0)
    monkeypatch.setattr(upgrade.subprocess,'run',run)
    upgrade.restore_original(output/'plan.json')
    assert state['running'] and not (work/'PAUSE_NEW_ENTRIES').exists()
