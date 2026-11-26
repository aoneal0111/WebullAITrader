"""Explicit controls for existing, discovered native crypto PAPER containers."""
import json
import re
from pathlib import Path
import shutil
import subprocess
from app.strategy_lab.crypto_monitor import CryptoMonitorReader, read_json, container_identity


def host_path(value):
    """Translate Docker Desktop's host-mount spelling back to a Windows path."""
    value=str(value).replace('\\','/')
    match=re.match(r'^/(?:run/desktop/mnt/host|host_mnt|mnt)/([A-Za-z])/(.*)$',value)
    if match:value=match[1]+':/'+match[2]
    return Path(value)


def same_host_path(first,second):
    a=str(host_path(first)).replace('\\','/').rstrip('/')
    b=str(host_path(second)).replace('\\','/').rstrip('/')
    if re.match(r'^[A-Za-z]:/',a) and re.match(r'^[A-Za-z]:/',b):return a.casefold()==b.casefold()
    return host_path(first).resolve()==host_path(second).resolve()


class CryptoWorkspaceControl:
    def __init__(self, reader=None, *, run=subprocess.run, docker=None):
        self.reader = reader or CryptoMonitorReader()
        self.run = run
        self.docker = docker or shutil.which('docker')

    def verified(self, work):
        work = Path(work).resolve()
        if work not in self.reader.discover():
            raise ValueError('UNDISCOVERED_PAPER_WALLET')
        config = read_json(work / ('resolved-config.json' if (work / 'resolved-config.json').is_file() else 'config.json'))
        profile = read_json(work / 'profile.json') or read_json(work / 'paper-manifest.json')
        if config.get('dry_run') is not True or profile.get('live_trading_enabled') is not False:
            raise ValueError('PAPER_SCOPE_REQUIRED')
        name = container_identity(work, profile)
        if not name or not self.docker:
            raise ValueError('MANAGED_CONTAINER_REQUIRED')
        template = '{"running":{{json .State.Running}},"mounts":{{json .Mounts}}}'
        result = self.run([self.docker, 'inspect', '--type', 'container', '--format', template, name],
            capture_output=True, text=True, timeout=5, check=False)
        if result.returncode != 0 or len(result.stdout) > 65536:
            raise ValueError('CONTAINER_IDENTITY_UNVERIFIED')
        observed = json.loads(result.stdout)
        if not any(m.get('Type') == 'bind' and m.get('Destination') == '/work'
                   and same_host_path(m.get('Source',''),work) for m in observed.get('mounts', [])):
            raise ValueError('CONTAINER_WALLET_MISMATCH')
        if type(observed.get('running')) is not bool:
            raise ValueError('CONTAINER_STATE_UNVERIFIED')
        return work, name, observed['running']

    def execute(self, action, work=None):
        if action not in ('start', 'stop', 'emergency'):
            raise ValueError('UNKNOWN_CRYPTO_ACTION')
        targets = self.reader.discover() if action == 'emergency' else (work,)
        results = []
        for target in targets:
            previous_markers=None;pause_warning=None
            try:
                wallet, name, running = self.verified(target)
                if action == 'start':
                    if running:raise ValueError('ALREADY_RUNNING_USE_EXISTING_WORKER')
                    # Explicit start resumes entries in this same saved wallet.
                    previous_markers={m:(wallet/m).read_bytes() if (wallet/m).is_file() else None
                        for m in ('PAUSE_NEW_ENTRIES','STOP')}
                    for marker in ('PAUSE_NEW_ENTRIES', 'STOP'):
                        (wallet / marker).unlink(missing_ok=True)
                    command = [self.docker, 'start', name]
                else:
                    try:(wallet / 'PAUSE_NEW_ENTRIES').write_text('ATLAS_GUI_OPERATOR_STOP\n', encoding='utf-8')
                    except OSError:pause_warning='ENTRY_PAUSE_MARKER_UNWRITABLE'
                    command = [self.docker, 'stop', '-t', '10' if action == 'emergency' else '120', name]
                result = self.run(command, capture_output=True, text=True,
                    timeout=30 if action == 'start' else 150, check=False)
                _, _, after = self.verified(wallet)
                success = result.returncode == 0 and after == (action == 'start')
                if action=='start' and not success:
                    (wallet/'PAUSE_NEW_ENTRIES').write_text('START_NOT_VERIFIED\n',encoding='utf-8')
                    if previous_markers.get('STOP') is not None:(wallet/'STOP').write_bytes(previous_markers['STOP'])
                results.append(dict(work=str(wallet), container=name, success=success,
                    state='RUNNING' if after else 'STOPPED', warning=pause_warning,error=None if success else 'COMMAND_OR_STATE_CHECK_FAILED'))
            except Exception as exc:
                if previous_markers is not None:
                    for marker,contents in previous_markers.items():
                        if contents is not None:(wallet/marker).write_bytes(contents)
                    (wallet/'PAUSE_NEW_ENTRIES').write_text('START_NOT_VERIFIED\n',encoding='utf-8')
                results.append(dict(work=str(target), success=False, error=type(exc).__name__ + ':' + str(exc)[:120]))
        return dict(action=action, success=bool(results) and all(r['success'] for r in results),
            results=results, positions_sold=False,
            note='Worker stop retains holdings; stopped workers do not manage exits. Equity is separate.')
