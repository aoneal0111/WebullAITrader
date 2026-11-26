"""BB code-only repair staging; preserve configuration and trading ledger."""
import argparse
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from uuid import uuid4
from app.strategy_lab.crypto_fleet import digest, write
from app.strategy_lab.crypto_fleet_runtime import verify


def stage(work, shadow):
    work,shadow=Path(work),Path(shadow)
    _,profile=verify(work)
    if profile['key']!='bb':raise ValueError('BB_ONLY')
    shadow.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(work/'config.json',shadow/'config.json')
    shutil.copytree(work/'upstream',shadow/'upstream')
    clean=deepcopy(profile)
    for key in ('generated_files','freqtrade_version'):clean.pop(key,None)
    clean['compiled']=False
    write(shadow/'profile.json',clean)


def apply(fleet, shadow, source):
    """Caller must stop BB first. Never read, copy, replace or reset a ledger."""
    fleet,shadow,source=Path(fleet),Path(shadow),Path(source).resolve()
    work=fleet/'bb';_,old=verify(work);_,new=verify(shadow)
    if old['key']!='bb' or new['key']!='bb':raise ValueError('BB_ONLY')
    fields=('config_sha256','source_files','pairs','fleet_id','strategy','timeframe')
    if any(old.get(k)!=new.get(k) for k in fields):raise ValueError('CODE_ONLY_REPAIR_REQUIRED')
    if not (work/'PAUSE_NEW_ENTRIES').is_file():raise ValueError('ENTRY_PAUSE_REQUIRED')
    if not (source/'app/strategy_lab/crypto_fleet_runtime.py').is_file():raise ValueError('REVIEWED_SOURCE_REQUIRED')
    compose_path=fleet/'compose.json';compose=json.loads(compose_path.read_text())
    changed=deepcopy(compose)
    mounts=[m for m in changed['services']['bb']['volumes'] if m.get('target')=='/check']
    if len(mounts)!=1 or not mounts[0].get('read_only'):raise ValueError('EXACT_READONLY_CHECK_MOUNT_REQUIRED')
    mounts[0]['source']=str(source)
    backup=work/('before-tag-repair-'+uuid4().hex);backup.mkdir()
    shutil.copytree(work/'strategies',backup/'strategies')
    shutil.copyfile(work/'profile.json',backup/'profile.json')
    shutil.copyfile(compose_path,backup/'compose.json')
    write(backup/'repair-manifest.json',dict(created_at=datetime.now(UTC).isoformat(),
        config_sha256=digest(work/'config.json'),ledger_modified=False,
        old_generated_files=old['generated_files'],new_generated_files=new['generated_files']))
    # BB is stopped: code/profile writes cannot race its worker. If a write
    # fails, restore only code/profile/compose, never database files.
    try:
        shutil.rmtree(work/'strategies');shutil.copytree(shadow/'strategies',work/'strategies')
        shutil.copyfile(shadow/'profile.json',work/'profile.json')
        write(compose_path,changed)
        verify(work)
    except Exception:
        shutil.rmtree(work/'strategies',ignore_errors=True);shutil.copytree(backup/'strategies',work/'strategies')
        shutil.copyfile(backup/'profile.json',work/'profile.json')
        shutil.copyfile(backup/'compose.json',compose_path)
        raise
    return dict(status='BB_TAG_CODE_REPAIRED',backup=str(backup),ledger_modified=False,
                configuration_modified=False,other_services_modified=False)


def capture_docker_logs(container, output, *, runner=None):
    """Capture native stderr as bytes, outside PowerShell's error stream."""
    import subprocess
    runner = runner or subprocess.run
    with Path(output).open('xb') as stream:
        runner(['docker', 'logs', '--tail', '1000', container],
               stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=30)
    return dict(status='BB_LOGS_CAPTURED', path=str(output))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fleet',type=Path,required=True)
    p.add_argument('--shadow',type=Path,required=True);p.add_argument('--source',type=Path)
    p.add_argument('--capture-container');p.add_argument('--log-output',type=Path)
    a=p.parse_args()
    if a.capture_container:
        if a.log_output is None:p.error('--log-output required')
        print(json.dumps(capture_docker_logs(a.capture_container,a.log_output),indent=2))
    elif a.source:print(json.dumps(apply(a.fleet,a.shadow,a.source),indent=2))
    else:stage(a.fleet/'bb',a.shadow)

if __name__=='__main__':main()
