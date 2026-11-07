"""Local opt-in for daily recording; preserve every other configuration line."""
from datetime import UTC, datetime
import argparse
import os
from pathlib import Path
import re
from uuid import uuid4


def enable(repo):
    from dotenv import dotenv_values
    from app.configuration import load_configuration
    repo=Path(repo).resolve();path=repo/'.env'
    raw=path.read_bytes() if path.exists() else b''
    text=raw.decode('utf-8-sig')
    pattern=r'(?m)^\s*(?:export\s+)?ATLAS_DAILY_RESEARCH_ENABLED\s*=.*$'
    if len(re.findall(pattern,text))>1:raise ValueError('DUPLICATE_RECORDING_SETTING')
    values={**dotenv_values(path),**os.environ}
    c=load_configuration(values)
    if c.environment.value!='PAPER' or c.live_trading_enabled or c.grok_paper_entries_enabled:
        raise ValueError('PAPER_AND_DISABLED_LIVE_GROK_ORDER_REVIEW_REQUIRED')
    inherited=os.environ.get('ATLAS_DAILY_RESEARCH_ENABLED')
    if inherited is not None and inherited.strip().lower() not in ('true','1','yes','on'):
        raise ValueError('INHERITED_ENVIRONMENT_OVERRIDES_RECORDING')
    replacement='ATLAS_DAILY_RESEARCH_ENABLED=true'
    updated=re.sub(pattern,replacement,text) if re.search(pattern,text) else text.rstrip('\r\n')+'\n'+replacement+'\n'
    target=updated.encode('utf-8')
    if target!=raw:
        backups=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'AtlasResearch/private-config-backups'
        backups.mkdir(parents=True,exist_ok=True)
        backup=backups/('before-daily-recording-'+uuid4().hex+'.env')
        with backup.open('xb') as stream:stream.write(raw)
        try:backup.chmod(0o600)
        except OSError:pass
        tmp=path.with_name('.env.daily-recording-'+uuid4().hex+'.tmp')
        try:
            with tmp.open('xb') as stream:stream.write(target)
            try:tmp.chmod(0o600)
            except OSError:pass
            if path.exists() and path.read_bytes()!=raw:raise ValueError('CONFIG_CHANGED_DURING_SETUP')
            tmp.replace(path)
        finally:
            if tmp.exists():tmp.unlink()
    else:backup=None
    return backup


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    args=parser.parse_args()
    backup=enable(args.repo)
    print('DAILY_RECORDING_ENABLED; NORMAL_ATLAS_SHORTCUT; NO_ATLAS_OR_GROK_STARTED')
    if backup:print('PRIVATE_CONFIG_BACKUP='+str(backup))


if __name__=='__main__':main()
