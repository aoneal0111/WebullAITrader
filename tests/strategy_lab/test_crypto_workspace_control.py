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
        overlay='overlay.json',service='boll',config_hashes={'config.json':sha256(original).hexdigest()})]
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


def test_prepare_creates_only_file_overlays_and_preserves_original_sources(tmp_path,monkeypatch):
    from app.strategy_lab import crypto_workspace_upgrade as upgrade
    work,c,calls,state=controller(tmp_path)
    source=tmp_path/'original';folder=source/'app/strategy_lab';folder.mkdir(parents=True)
    original={}
    for name in ('nfi_forward_engine.py','crypto_fleet_runtime.py'):
        original[name]=Path(upgrade.__file__).with_name(name).read_text()
        (folder/name).write_text(original[name])
    compose=tmp_path/'compose.json';compose.write_text('{}')
    reader=c.reader
    reader.read=lambda:dict(engines=[dict(work=str(work),process_liveness_verified=True)])
    monkeypatch.setattr(upgrade,'CryptoMonitorReader',lambda base:reader)
    monkeypatch.setattr(upgrade,'CryptoWorkspaceControl',lambda reader:c)
    def run(command,**kwargs):
        return NS(returncode=0,stdout=json.dumps(dict(mounts=[dict(Type='bind',Source=str(source),
            Destination='/check',RW=False)],files=str(compose),service='boll')))
    monkeypatch.setattr(upgrade.subprocess,'run',run)
    monkeypatch.setattr(upgrade,'verify_overlay',lambda *args:None)
    plan=upgrade.prepare(tmp_path/'output')
    assert len(plan)==1
    for name,raw in original.items():assert (folder/name).read_text()==raw
    overlay=json.loads(Path(plan[0]['overlay']).read_text())
    volumes=overlay['services']['boll']['volumes']
    assert len(volumes)==3 and all(v['read_only'] for v in volumes)
    assert all(v['target'].startswith('/check/app/strategy_lab/') for v in volumes)
    assert (work/'config.json').read_text()=='{"dry_run": true}'


def test_compose_verification_rejects_any_policy_or_wallet_mount_change(tmp_path,monkeypatch):
    from copy import deepcopy
    from app.strategy_lab import crypto_workspace_upgrade as upgrade
    overlay=tmp_path/'overlay.json'
    target='/check/app/strategy_lab/crypto_workspace_telemetry.py'
    mount=dict(type='bind',source=str(tmp_path/'helper.py'),target=target,read_only=True)
    overlay.write_text(json.dumps(dict(services=dict(boll=dict(volumes=[mount])))))
    before=dict(name='same-project',services=dict(boll=dict(image='pinned',command=['same','args'],
        volumes=[dict(type='bind',source='wallet',target='/work')],environment={'LIVE':'false'})))
    after=deepcopy(before);after['services']['boll']['volumes'].append(mount)
    def check(updated):
        def run(cmd,**kw):return NS(returncode=0,stdout=json.dumps(updated if str(overlay) in cmd else before))
        monkeypatch.setattr(upgrade.subprocess,'run',run)
        upgrade.verify_overlay('docker',['original.json'],overlay,'boll')
    check(after)
    changed=deepcopy(after);changed['services']['boll']['command']=['different']
    with pytest.raises(ValueError,match='NON_TELEMETRY'):check(changed)
    changed=deepcopy(after);changed['services']['boll']['volumes'][0]['source']='other-wallet'
    with pytest.raises(ValueError,match='NON_TELEMETRY'):check(changed)
