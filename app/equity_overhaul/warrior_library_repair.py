"""Explicit local migration of an existing, stopped continuous PAPER operation."""
import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from .paper_experiment import load, CONTINUOUS_SCOPE, WARRIOR_LIBRARY_POLICY, warrior_library_families, validate_continuous


def migrate(directory):
    root=Path(directory).resolve()
    path,spec=load(root/'experiment.json',allow_expired=True)
    if spec.get('scope')!=CONTINUOUS_SCOPE:raise ValueError('CONTINUOUS_PAPER_REQUIRED')
    if spec.get('warrior_library_policy')==WARRIOR_LIBRARY_POLICY:
        return {'status':'ALREADY_CONFIGURED','operation_id':spec['trial_id']}
    original=path.read_bytes()
    changed={**spec,'warrior_library_policy':WARRIOR_LIBRARY_POLICY,
             'families':{**spec['families'],'WARRIOR_MOMENTUM':warrior_library_families()}}
    validate_continuous(changed,datetime.now(UTC))
    backup=path.with_name('experiment-before-warrior-library-'+uuid4().hex+'.json')
    with backup.open('xb') as f:f.write(original)
    prepared=path.with_name('experiment-library-'+uuid4().hex+'.partial')
    try:
        with prepared.open('x',encoding='utf-8') as f:json.dump(changed,f,indent=2)
        if path.read_bytes()!=original:raise ValueError('OPERATING_SPEC_CHANGED_DURING_REPAIR')
        prepared.replace(path)
    finally:
        prepared.unlink(missing_ok=True)
    return {'status':'WARRIOR_LIBRARY_PREPARED','operation_id':spec['trial_id'],
            'spec_backup':str(backup),'previous_sha256':sha256(original).hexdigest(),
            'new_sha256':sha256(path.read_bytes()).hexdigest(),
            'live_trading_enabled':False,'orders':0,'api_requests':0}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--paper',required=True)
    result=migrate(parser.parse_args().paper);print(json.dumps(result,indent=2))


if __name__=='__main__':main()
