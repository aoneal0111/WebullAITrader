"""Code-only preparation/apply for existing crypto paper workers and ledgers."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
from app.strategy_lab.nfi_source_reproduction import IMAGE


def digest(path):return sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')


def plan(fleet,addon,source,output):
    fleet,addon,source,output=map(lambda p:Path(p).resolve(),(fleet,addon,source,output))
    output.mkdir(parents=True,exist_ok=False);shared=fleet/'shared'
    report=dict(scope='CRYPTO_PUBLIC_TRANSPORT_CODE_REPAIR_V1',source=str(source),groups=[],
        wallets_reset=False,strategy_parameters_changed=False,equity_changed=False)
    for base,keys in [(fleet/'compose.json',('e0','nasos','bb','crsi','wave','nfi')),
                      (addon/'compose.json',('crypto',))]:
        original=read(base);updated=deepcopy(original);services=[]
        for key in keys:
            old=original['services'][key]
            if old['image']!=IMAGE:raise ValueError('PINNED_IMAGE_REQUIRED')
            volumes=updated['services'][key]['volumes']
            mounts={v['target']:v for v in volumes if isinstance(v,dict)}
            if not {'/check','/work','/shared'}<=mounts.keys():raise ValueError('EXPECTED_PAPER_MOUNTS_REQUIRED')
            work=Path(mounts['/work']['source']);config=work/('resolved-config.json' if key=='nfi' else 'config.json')
            cfg=read(config)
            if cfg.get('dry_run') is not True or cfg.get('trading_mode')!='spot':raise ValueError('EXISTING_SPOT_PAPER_REQUIRED')
            if cfg['exchange']['name']!='kraken':raise ValueError('EXISTING_KRAKEN_REQUIRED')
            mounts['/check'].update(source=str(source),read_only=True)
            mounts['/shared']['source']=str(shared)
            sealed=[config,*sorted((work/'strategies').glob('*.py'))]
            for name in ('profile.json','paper-manifest.json','resolved-universe.json'):
                if (work/name).exists():sealed.append(work/name)
            services.append(dict(key=key,name=old['container_name'] if 'container_name' in old else None,
                work=str(work),ledger='nfi-paper.sqlite3' if key=='nfi' else 'paper.sqlite3',
                sealed_sha256={str(p):digest(p) for p in sealed}))
        proposed=output/('fleet-compose.json' if base.parent==fleet else 'addon-compose.json')
        write(proposed,updated)
        report['groups'].append(dict(base=str(base),base_sha256=digest(base),
            proposed=str(proposed),proposed_sha256=digest(proposed),services=services))
    # The addon crypto service may use an auto-generated Compose container name.
    write(output/'repair-plan.json',report);return report


def stopped(name):
    result=subprocess.run(['docker','inspect','--format','{{.State.Running}}',name],capture_output=True,text=True)
    return result.returncode==0 and result.stdout.strip()=='false'


def apply(output,*,is_stopped=stopped):
    output=Path(output);report=read(output/'repair-plan.json')
    if report.get('scope')!='CRYPTO_PUBLIC_TRANSPORT_CODE_REPAIR_V1':raise ValueError('REPAIR_PLAN_REQUIRED')
    if (output/'applied.json').exists():raise ValueError('REPAIR_ALREADY_APPLIED')
    # Validate every group before making the first replacement.
    for group in report['groups']:
        if digest(group['base'])!=group['base_sha256'] or digest(group['proposed'])!=group['proposed_sha256']:
            raise ValueError('COMPOSE_CHANGED_SINCE_PREFLIGHT')
        for service in group['services']:
            if not service.get('name') or not is_stopped(service['name']):raise ValueError('ALL_SELECTED_CRYPTO_WORKERS_MUST_BE_STOPPED')
            for path,expected in service['sealed_sha256'].items():
                if digest(path)!=expected:raise ValueError('SEALED_STRATEGY_OR_CONFIG_CHANGED')
    backups=output/'backups';backups.mkdir()
    ledgers=[]
    for i,group in enumerate(report['groups']):
        (backups/('compose-'+str(i)+'.json')).write_bytes(Path(group['base']).read_bytes())
        for service in group['services']:
            path=Path(service['work'])/service['ledger']
            if path.exists():
                before=digest(path);db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
                destination=backups/(str(i)+'-'+service['key']+'.sqlite3');target=sqlite3.connect(destination)
                try:db.backup(target)
                finally:target.close();db.close()
                if digest(path)!=before:raise ValueError('SOURCE_LEDGER_CHANGED_DURING_BACKUP')
                ledgers.append(dict(path=str(path),sha256=before,backup=str(destination)))
    changed=[]
    try:
        for group in report['groups']:
            base=Path(group['base']);temp=base.with_suffix('.transport-repair.tmp')
            temp.write_bytes(Path(group['proposed']).read_bytes());temp.replace(base);changed.append(group)
        for row in ledgers:
            if digest(row['path'])!=row['sha256']:raise ValueError('SOURCE_LEDGER_CHANGED')
    except BaseException:
        for group in changed:
            index=report['groups'].index(group)
            Path(group['base']).write_bytes((backups/('compose-'+str(index)+'.json')).read_bytes())
        raise
    write(output/'applied.json',dict(status='CODE_MOUNTS_UPDATED',ledgers=ledgers,
        workers_started=False,original_strategy_and_config_hashes_retained=True))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    p.add_argument('--fleet');p.add_argument('--addon');p.add_argument('--source');p.add_argument('--apply',action='store_true')
    a=p.parse_args()
    if a.apply:apply(a.output)
    else:plan(a.fleet,a.addon,a.source,a.output)
