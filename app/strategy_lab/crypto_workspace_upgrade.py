"""Prepare telemetry-only bind overlays for existing Docker PAPER workers."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
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
    return raw


def verify_overlay(docker, files, overlay, service):
    def rendered(paths):
        command=[docker,'compose']
        for file in paths:command+=['-f',str(file)]
        result=subprocess.run(command+['config','--format','json'],capture_output=True,text=True,timeout=20,check=False)
        if result.returncode or len(result.stdout)>8*1024**2:raise ValueError('COMPOSE_VALIDATION_FAILED')
        return json.loads(result.stdout)
    original=rendered(files);updated=rendered(files+[str(overlay)])
    before=original['services'][service];after=updated['services'][service]
    expected=json.loads(Path(overlay).read_text(encoding='utf-8'))['services'][service]['volumes']
    allowed={v['target']:v for v in expected}
    before_volumes={v['target']:v for v in before.get('volumes',[])}
    after_volumes={v['target']:v for v in after.get('volumes',[])}
    for target,wanted in allowed.items():
        actual=after_volumes.get(target,{})
        if actual.get('type')!='bind' or actual.get('read_only') is not True or not same_host_path(actual.get('source',''),wanted['source']):
            raise ValueError('TELEMETRY_BIND_NOT_RESOLVED')
    before['volumes']={k:v for k,v in before_volumes.items() if k not in allowed}
    after['volumes']={k:v for k,v in after_volumes.items() if k not in allowed}
    if original!=updated:raise ValueError('NON_TELEMETRY_COMPOSE_CHANGE')


def prepare(output, base=None):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    reader=CryptoMonitorReader(base);control=CryptoWorkspaceControl(reader)
    if not control.docker:raise ValueError('DOCKER_REQUIRED')
    plan=[]
    existing=[Path(row['work']) for row in reader.read()['engines'] if row.get('process_liveness_verified')]
    for index,work in enumerate(existing):
        wallet,name,running=control.verified(work)
        template='{"mounts":{{json .Mounts}},"files":{{json (index .Config.Labels "com.docker.compose.project.config_files")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
        result=subprocess.run([control.docker,'inspect','--type','container','--format',template,name],
            capture_output=True,text=True,timeout=5,check=True)
        metadata=json.loads(result.stdout)
        mounts=metadata['mounts']
        source=next((host_path(m['Source']) for m in mounts if m.get('Type')=='bind' and m.get('Destination')=='/check' and m.get('RW') is False),None)
        files=[str(Path(f).resolve()) for f in (metadata.get('files') or '').split(',') if f]
        service=metadata.get('service')
        if not source or not files or not service or not all(Path(f).is_file() for f in files):
            raise ValueError('EXISTING_COMPOSE_AND_READ_ONLY_SOURCE_REQUIRED')
        folder=output/str(index);folder.mkdir()
        volumes=[]
        for filename in ('nfi_forward_engine.py','crypto_fleet_runtime.py','crypto_workspace_telemetry.py'):
            target=folder/filename
            if filename=='crypto_workspace_telemetry.py':
                shutil.copyfile(Path(__file__).with_name(filename),target)
            else:
                original=source/'app/strategy_lab'/filename
                raw=original.read_text(encoding='utf-8')
                # Existing nested overlays take priority over the parent source.
                nested=next((m for m in mounts if m.get('Destination')=='/check/app/strategy_lab/'+filename),None)
                if nested:raw=host_path(nested['Source']).read_text(encoding='utf-8')
                target.write_text(patch_engine(raw,filename),encoding='utf-8')
            compile(target.read_text(),str(target),'exec')
            volumes.append(dict(type='bind',source=str(target),target='/check/app/strategy_lab/'+filename,read_only=True))
        overlay=folder/'compose-telemetry.json'
        overlay.write_text(json.dumps(dict(services={service:dict(volumes=volumes)})),encoding='utf-8')
        verify_overlay(control.docker,files,overlay,service)
        configs={p.name:sha256(p.read_bytes()).hexdigest() for p in (wallet/'config.json',wallet/'resolved-config.json',wallet/'profile.json',wallet/'paper-manifest.json') if p.is_file()}
        plan.append(dict(work=str(wallet),container=name,was_running=running,files=files,
            overlay=str(overlay),service=service,config_hashes=configs))
    if not plan:raise ValueError('NO_MANAGED_CRYPTO_WORKERS')
    (output/'plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    return plan


def apply(output):
    output=Path(output).resolve()
    plan=json.loads((output/'plan.json').read_text(encoding='utf-8'))
    control=CryptoWorkspaceControl()
    for item in plan:
        work,name,running=control.verified(item['work'])
        if name!=item['container']:raise ValueError('CONTAINER_CHANGED_SINCE_PREPARATION')
        for filename,expected in item['config_hashes'].items():
            if sha256((work/filename).read_bytes()).hexdigest()!=expected:raise ValueError('WALLET_CONFIG_CHANGED')
        verify_overlay(control.docker,item['files'],item['overlay'],item['service'])
        marker=work/'PAUSE_NEW_ENTRIES'
        paused=marker.read_bytes() if marker.is_file() else None
        stopped=control.execute('stop',str(work))
        if not stopped['success']:raise ValueError('WORKER_STOP_NOT_CONFIRMED')
        command=[control.docker,'compose']
        for file in item['files']+[item['overlay']]:command+=['-f',file]
        command+=['up','--no-start','--no-deps','--force-recreate',item['service']]
        subprocess.run(command,check=True,timeout=120)
        control.verified(work)
        if paused is None:marker.unlink(missing_ok=True)
        else:marker.write_bytes(paused)
        if running:
            subprocess.run([control.docker,'start',name],check=True,timeout=30)
            if not control.verified(work)[2]:raise ValueError('WORKER_START_NOT_CONFIRMED')
        for filename,expected in item['config_hashes'].items():
            if sha256((work/filename).read_bytes()).hexdigest()!=expected:raise ValueError('WALLET_CONFIG_CHANGED')
        print('TELEMETRY_INSTALLED='+name+'; PREVIOUS_RUNNING_STATE='+str(running),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--apply',action='store_true');a=p.parse_args()
    if a.apply:apply(a.output)
    else:
        plan=prepare(a.output)
        print('PREPARED_CRYPTO_TELEMETRY_OVERLAYS='+str(len(plan))+'; WORKERS_UNCHANGED')

if __name__=='__main__':main()
