"""Prepare telemetry-only bind overlays for existing Docker PAPER workers."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import stat
import subprocess
from app.strategy_lab.crypto_monitor import CryptoMonitorReader
from app.strategy_lab.crypto_workspace_control import CryptoWorkspaceControl, host_path, same_host_path


def patch_engine(raw, name):
    # Apply only the display hooks to the source each container already uses.
    # Refuse unfamiliar sources rather than replacing strategy/runtime policy.
    if name == 'nfi_forward_engine.py':
        old='                        for subject,response in parts:\n'
        new=old+'                            from app.strategy_lab.crypto_workspace_telemetry import observe\n                            observe(self,method,subject,response)\n'
        if 'crypto_workspace_telemetry import observe' not in raw:
            if raw.count(old)!=1:raise ValueError('UNRECOGNIZED_QUOTE_HOOK')
            raw=raw.replace(old,new)
        old='            evidence.capture(bot,now)\n'
        new=old+'            from app.strategy_lab.crypto_workspace_telemetry import publish\n            publish(work,bot,evidence,now)\n'
    else:
        old='                now=datetime.now(UTC);evidence.capture(bot,now)\n'
        new=old+'                from app.strategy_lab.crypto_workspace_telemetry import publish\n                publish(work,bot,evidence,now)\n'
    if 'crypto_workspace_telemetry import publish' not in raw:
        if raw.count(old)!=1:raise ValueError('UNRECOGNIZED_CYCLE_HOOK')
        raw=raw.replace(old,new)
    if 'crypto_status_io import publish_status' not in raw:
        if name=='crypto_fleet_runtime.py':
            initial="        write(work/'engine-status.json',status)"
            cycle="                temporary=work/'engine-status.json.tmp';write(temporary,status);temporary.replace(work/'engine-status.json')"
            final="                write(work/'engine-status.json',status)"
            if raw.count(cycle)!=1 or raw.count(initial)!=2:raise ValueError('UNRECOGNIZED_STATUS_PUBLICATION')
            raw=raw.replace(initial,"        from app.strategy_lab.crypto_status_io import publish_status\n        publish_status(work/'engine-status.json',status,evidence)",1)
            raw=raw.replace(cycle,"                publish_status(work/'engine-status.json',status,evidence)")
            raw=raw.replace(final,"                publish_status(work/'engine-status.json',status,evidence)")
        else:
            old="            try:\n                temporary=work/'engine-status.json.tmp'\n                write(temporary,status);temporary.replace(work/'engine-status.json')\n            except OSError:evidence.error='STATUS_WRITE_FAILED'"
            if raw.count(old)!=1:raise ValueError('UNRECOGNIZED_STATUS_PUBLICATION')
            raw=raw.replace(old,"            from app.strategy_lab.crypto_status_io import publish_status\n            publish_status(work/'engine-status.json',status,evidence)")
    return raw


MODULE_TARGET = '/check/app/strategy_lab'


def inside_module(target):
    return target == MODULE_TARGET or target.startswith(MODULE_TARGET + '/')


def compose_command(docker, files):
    command=[docker,'compose']
    for file in files:command+=['-f',str(file)]
    return command


def rendered(docker, files):
    result=subprocess.run(compose_command(docker,files)+['config','--format','json'],
        capture_output=True,text=True,timeout=20,check=False)
    if result.returncode or len(result.stdout)>8*1024**2:raise ValueError('COMPOSE_VALIDATION_FAILED')
    return json.loads(result.stdout)


def verify_overlay(docker, files, overlay, service):
    original=rendered(docker,files);updated=rendered(docker,files+[str(overlay)])
    before=original['services'][service];after=updated['services'][service]
    manifest=json.loads(Path(overlay).with_name('overlay-manifest.json').read_text(encoding='utf-8'))
    before_volumes={v['target']:v for v in before.get('volumes',[])}
    after_volumes={v['target']:v for v in after.get('volumes',[])}
    mounts=[v for target,v in after_volumes.items() if inside_module(target)]
    if len(mounts)!=1:raise ValueError('NESTED_MODULE_FILE_MOUNTS_FORBIDDEN')
    actual=mounts[0]
    if (actual.get('target')!=MODULE_TARGET or actual.get('type')!='bind' or actual.get('read_only') is not True
        or not same_host_path(actual.get('source',''),manifest['module_source'])):
        raise ValueError('TELEMETRY_DIRECTORY_BIND_NOT_RESOLVED')
    before['volumes']={k:v for k,v in before_volumes.items() if not inside_module(k)}
    after['volumes']={k:v for k,v in after_volumes.items() if not inside_module(k)}
    if original!=updated:raise ValueError('NON_TELEMETRY_COMPOSE_CHANGE')
    bundle=Path(manifest['module_source'])
    for name,expected in manifest['bundle_hashes'].items():
        if sha256((bundle/name).read_bytes()).hexdigest()!=expected:raise ValueError('TELEMETRY_BUNDLE_CHANGED')


def copy_module(source, bundle, mounts):
    # The destination directory already exists under the read-only /check mount.
    # Copy its existing contents first; the only edits are the two display hooks
    # and the helper. No nonexistent child mountpoint needs to be created there.
    source=source/'app/strategy_lab'
    if not source.is_dir() or source.is_symlink():raise ValueError('EXISTING_MODULE_DIRECTORY_REQUIRED')
    entries=list(source.rglob('*'))
    if len(entries)>4096 or any(p.is_symlink() for p in entries):raise ValueError('MODULE_COPY_BOUND')
    if sum(p.stat().st_size for p in entries if p.is_file())>64*1024**2:raise ValueError('MODULE_COPY_BOUND')
    shutil.copytree(source,bundle,ignore=shutil.ignore_patterns('__pycache__'))
    # Retain any overlays already used by the old worker before patching.
    for mount in sorted(mounts,key=lambda m:len(m.get('Destination',''))):
        destination=mount.get('Destination','')
        if not inside_module(destination) or mount.get('Type')!='bind':continue
        old=host_path(mount['Source'])
        relative=destination[len(MODULE_TARGET):].lstrip('/')
        target=bundle/relative if relative else bundle
        if old.is_dir():shutil.copytree(old,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
        elif old.is_file():
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(old,target)
        else:raise ValueError('OLD_MODULE_BIND_SOURCE_MISSING')
    # Only the private copy is made writable for patching; the Docker mount
    # remains read-only and the original source attributes are untouched.
    for directory in [bundle]+[p for p in bundle.rglob('*') if p.is_dir()]:
        directory.chmod(directory.stat().st_mode|stat.S_IWUSR|stat.S_IXUSR)
    before={str(p.relative_to(bundle)):sha256(p.read_bytes()).hexdigest() for p in bundle.rglob('*') if p.is_file()}
    changed={'nfi_forward_engine.py','crypto_fleet_runtime.py','crypto_workspace_telemetry.py','crypto_status_io.py'}
    for filename in changed:
        target=bundle/filename
        if target.exists():target.chmod(target.stat().st_mode|stat.S_IWUSR)
        if filename in {'crypto_workspace_telemetry.py','crypto_status_io.py'}:shutil.copyfile(Path(__file__).with_name(filename),target)
        else:target.write_text(patch_engine(target.read_text(encoding='utf-8'),filename),encoding='utf-8')
        compile(target.read_text(encoding='utf-8'),str(target),'exec')
    after={str(p.relative_to(bundle)):sha256(p.read_bytes()).hexdigest() for p in bundle.rglob('*') if p.is_file()}
    if any(after.get(name)!=digest for name,digest in before.items() if name not in changed):
        raise ValueError('NON_TELEMETRY_MODULE_CHANGE')
    return after


def prepare(output, base=None, *, recover_plan=None):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    previous={}
    if recover_plan:
        recovery=Path(recover_plan)
        if recovery.stat().st_size>256*1024:raise ValueError('RECOVERY_PLAN_BOUND')
        rows=json.loads(recovery.read_text(encoding='utf-8'))
        for item in rows:
            if type(item.get('was_running')) is not bool:raise ValueError('RECOVERY_STATE_REQUIRED')
            key=str(Path(item['work']).resolve())
            if key in previous:raise ValueError('DUPLICATE_RECOVERY_WALLET')
            previous[key]=item
    reader=CryptoMonitorReader(base);control=CryptoWorkspaceControl(reader)
    if not control.docker:raise ValueError('DOCKER_REQUIRED')
    plan=[]
    existing=[Path(row['work']) for row in reader.read()['engines'] if row.get('process_liveness_verified')]
    for index,work in enumerate(existing):
        wallet,name,running=control.verified(work)
        template='{"mounts":{{json .Mounts}},"files":{{json (index .Config.Labels "com.docker.compose.project.config_files")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
        result=subprocess.run([control.docker,'inspect','--type','container','--format',template,name],
            capture_output=True,text=True,timeout=5,check=True)
        metadata=json.loads(result.stdout);mounts=metadata['mounts']
        source=next((host_path(m['Source']) for m in mounts if m.get('Type')=='bind' and m.get('Destination')=='/check' and m.get('RW') is False),None)
        old=previous.get(str(wallet))
        if old:
            if old['container']!=name:raise ValueError('RECOVERY_CONTAINER_MISMATCH')
            files=old['files'];service=old['service']
            for filename,expected in old['config_hashes'].items():
                if sha256((wallet/filename).read_bytes()).hexdigest()!=expected:raise ValueError('RECOVERY_WALLET_CONFIG_CHANGED')
        else:
            files=[str(Path(f).resolve()) for f in (metadata.get('files') or '').split(',') if f]
            service=metadata.get('service')
        if not source or not files or not service or not all(Path(f).is_file() for f in files):
            raise ValueError('EXISTING_COMPOSE_AND_READ_ONLY_SOURCE_REQUIRED')
        folder=output/str(index);folder.mkdir();bundle=folder/'strategy_lab'
        hashes=copy_module(source,bundle,mounts)
        baseline=rendered(control.docker,files)['services'][service]
        volumes=[v for v in baseline.get('volumes',[]) if not inside_module(v['target'])]
        volumes.append(dict(type='bind',source=str(bundle),target=MODULE_TARGET,read_only=True))
        overlay=folder/'compose-telemetry.yaml'
        # !override removes the failed individual-file mounts instead of merging
        # them back in. Docker Compose validates support before any worker stops.
        overlay.write_text('services:\n  '+json.dumps(service)+':\n    volumes: !override '+json.dumps(volumes)+'\n',encoding='utf-8')
        (folder/'overlay-manifest.json').write_text(json.dumps(dict(module_source=str(bundle),bundle_hashes=hashes)),encoding='utf-8')
        verify_overlay(control.docker,files,overlay,service)
        configs={p.name:sha256(p.read_bytes()).hexdigest() for p in (wallet/'config.json',wallet/'resolved-config.json',wallet/'profile.json',wallet/'paper-manifest.json') if p.is_file()}
        item=dict(work=str(wallet),container=name,was_running=running,files=files,
            overlay=str(overlay),service=service,config_hashes=configs,mount_layout='EXISTING_MODULE_DIRECTORY_V2')
        if old:item['restore_running']=old['was_running']
        plan.append(item)
    if not plan:raise ValueError('NO_MANAGED_CRYPTO_WORKERS')
    if previous and not set(previous)<=set(p['work'] for p in plan):raise ValueError('RECOVERY_WALLET_NOT_DISCOVERED')
    (output/'plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    return plan


def restore_pause(work, paused):
    marker=work/'PAUSE_NEW_ENTRIES'
    if paused is None:marker.unlink(missing_ok=True)
    else:marker.write_bytes(paused)


def recreate(control,item,files,desired,paused):
    subprocess.run(compose_command(control.docker,files)+['up','--no-start','--no-deps','--force-recreate',item['service']],check=True,timeout=120)
    work,name,_=control.verified(item['work'])
    restore_pause(work,paused)
    if desired:
        subprocess.run([control.docker,'start',name],check=True,timeout=30)
    if control.verified(work)[2]!=desired:raise ValueError('WORKER_STATE_NOT_CONFIRMED')


def apply(output):
    output=Path(output).resolve();plan=json.loads((output/'plan.json').read_text(encoding='utf-8'))
    control=CryptoWorkspaceControl()
    # Validate the complete plan before touching the first worker.
    for item in plan:
        if item.get('mount_layout')!='EXISTING_MODULE_DIRECTORY_V2':raise ValueError('REPREPARE_DIRECTORY_OVERLAY_REQUIRED')
        work,name,_=control.verified(item['work'])
        if name!=item['container']:raise ValueError('CONTAINER_CHANGED_SINCE_PREPARATION')
        for filename,expected in item['config_hashes'].items():
            if sha256((work/filename).read_bytes()).hexdigest()!=expected:raise ValueError('WALLET_CONFIG_CHANGED')
        verify_overlay(control.docker,item['files'],item['overlay'],item['service'])
    for item in plan:
        work,name,running=control.verified(item['work'])
        desired=item.get('restore_running',running)
        marker=work/'PAUSE_NEW_ENTRIES';paused=marker.read_bytes() if marker.is_file() else None
        if not control.execute('stop',str(work))['success']:raise ValueError('WORKER_STOP_NOT_CONFIRMED')
        try:
            recreate(control,item,item['files']+[item['overlay']],desired,paused)
        except Exception:
            print('TELEMETRY_APPLY_FAILED='+name+'; RESTORING_PREVIOUS_COMPOSE',flush=True)
            try:
                recreate(control,item,item['files'],desired,paused)
                print('WORKER_ROLLBACK_RESTORED='+name+'; RUNNING='+str(desired),flush=True)
            except Exception:
                print('WORKER_ROLLBACK_FAILED='+name+'; CHECK_DOCKER_STATE',flush=True)
            raise
        print('TELEMETRY_INSTALLED='+name+'; PREVIOUS_RUNNING_STATE='+str(desired),flush=True)


def restore_original(recover_plan):
    """Recover a failed stopped worker before attempting the display update."""
    rows=json.loads(Path(recover_plan).read_text(encoding='utf-8'))
    control=CryptoWorkspaceControl()
    for item in rows:
        work,name,running=control.verified(item['work'])
        if name!=item['container'] or type(item.get('was_running')) is not bool:
            raise ValueError('RECOVERY_IDENTITY_REQUIRED')
        for filename,expected in item['config_hashes'].items():
            if sha256((work/filename).read_bytes()).hexdigest()!=expected:raise ValueError('RECOVERY_WALLET_CONFIG_CHANGED')
        if item['was_running'] and not running:
            # The saved list precedes the failed telemetry override.
            rendered(control.docker,item['files'])
            marker=work/'PAUSE_NEW_ENTRIES';paused=marker.read_bytes() if marker.is_file() else None
            recreate(control,item,item['files'],True,paused)
            print('ORIGINAL_WORKER_RECOVERED='+name+'; WALLET_AND_STRATEGY_RETAINED',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--restore-original',action='store_true');p.add_argument('--recover-plan',type=Path);a=p.parse_args()
    if a.restore_original:
        if not a.recover_plan:p.error('--recover-plan is required for recovery')
        restore_original(a.recover_plan)
    elif a.apply:apply(a.output)
    else:
        plan=prepare(a.output,recover_plan=a.recover_plan)
        print('PREPARED_CRYPTO_TELEMETRY_DIRECTORY_OVERLAYS='+str(len(plan))+'; WORKERS_UNCHANGED')

if __name__=='__main__':main()
