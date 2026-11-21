"""Offline bounded-clock policy comparison on selected, hashed PAPER evidence.

No fills, provider requests or profitability inference. Historical timestamps and
prices stay unchanged; baseline decisions must reproduce before comparison.
"""
import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from .scalper_evidence_audit import typed,reconstructed_preview,POLICY_FILES
from .scalper_repair_replay import source_binding
from app.equity_overhaul.paper_experiment import PaperExperiment
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig,QuickScalperPolicy,QuickScalpSnapshot


def compare(evidence):
    evidence=Path(evidence)
    if evidence.is_symlink() or evidence.stat().st_size>8*1024**2:raise ValueError('BOUNDED_EVIDENCE_REQUIRED')
    with zipfile.ZipFile(evidence) as archive:
        infos=archive.infolist()
        if len(infos)!=len({i.filename for i in infos}) or sum(i.file_size for i in infos)>32*1024**2:
            raise ValueError('EVIDENCE_ARCHIVE_BOUND')
        manifest=json.loads(archive.read('research-manifest.json'))
        review=json.loads(archive.read('original-paper-day-review.json'))
        summary=json.loads(archive.read('entry-evidence-summary.json'))
        if (manifest.get('live_trading_enabled') is not False
                or manifest.get('paper_experiment',{}).get('profile')!='GROK_WARRIOR_ACCOUNT_V6'
                or manifest['paper_experiment'].get('scalper_clock_policy') is not None
                or review['trial_id']!=summary['trial_id'] or review['trial_id']!=manifest['paper_experiment']['trial_id']
                or manifest['baseline_sha256']!=review['verified_prefix']['baseline_sha256']
                or sha256(archive.read('research-manifest.json')).hexdigest()!=review['files']['capture/research-manifest.json']['sha256']):
            raise ValueError('ORIGINAL_STRICT_ACCOUNT_PAPER_EVIDENCE_REQUIRED')
        source=source_binding(tuple(manifest['source_files'][p] for p in POLICY_FILES),Path(__file__).resolve().parents[2])
        if source!='PRIOR_V6':raise ValueError('REVIEWED_STRICT_V6_SOURCE_REQUIRED')
        # This pinned desktop constructs defaults + this exact experiment profile.
        # Reproducing all selected baseline outcomes below is mandatory.
        config=PaperExperiment.scalper_config(SimpleNamespace(cent_early=True,spec=manifest['paper_experiment']),QuickScalperConfig(enabled=True))
        base=QuickScalperPolicy(config)
        candidate=QuickScalperPolicy(replace(config,provider_future_tolerance_seconds=D('.25')))
        counts={'stream':Counter(),'confirmation':Counter()};mismatch=0;seen=0;total=0;previous=0
        with archive.open('selected-events.jsonl') as stream:
            while line:=stream.readline(128*1024+1):
                if len(line)>128*1024 or not line.endswith(b'\n'):raise ValueError('SELECTED_RECORD_BOUND')
                seen+=1;total+=len(line);row=json.loads(line);claimed=row.pop('sha256')
                if (row['sequence']<=previous or row['baseline_sha256']!=manifest['baseline_sha256']
                        or sha256(json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()!=claimed):
                    raise ValueError('SELECTED_RECORD_CHANGED')
                previous=row['sequence'];p=row['payload'];kind=None;expected=None
                if row['kind']=='STREAM_ASSESSMENT':
                    kind='stream';snap=typed(QuickScalpSnapshot,p['snapshot']);expected=p['reason']
                elif row['kind']=='ASSESSMENT' and p.get('engine')=='QUICK_SCALPER':
                    kind='confirmation';snap=reconstructed_preview(typed(QuickScalpSnapshot,p['preview']),p['quote'],datetime.fromisoformat(p['assessed_at']))
                if kind:
                    old,new=base.assess(snap),candidate.assess(snap)
                    counts[kind][old.reason+' -> '+new.reason]+=1
                    if kind=='stream':mismatch+=old.reason!=expected
                    else:
                        label={'EXECUTABLE':'ENTRY_CANDIDATE','WAIT':'WAIT','REJECTED_HARD_SAFETY':'BLOCKED'}[old.decision.value]
                        mismatch+=label!=p['baseline_result']
        if seen!=summary['selected_records'] or total!=summary['selected_bytes']:
            raise ValueError('SELECTED_COVERAGE_MISMATCH')
        if mismatch or not counts['stream'] or not counts['confirmation']:
            raise ValueError('BASELINE_CLOCK_REPLAY_MISMATCH')
    return dict(scope='OFFLINE_SCALPER_CLOCK_COMPARISON_V1',source_binding=source,
        baseline_reproduced=True,selected_records=seen,evidence_sha256=sha256(evidence.read_bytes()).hexdigest(),
        maximum_future_ms=250,maximum_book_age_ms=5000,
        transitions={k:dict(v) for k,v in counts.items()},orders=0,network_requests=0,
        provider_timestamps_modified=False,profitability_established=False,
        limitations=['Selected trade-window streams and recorded confirmations only, not full-day opportunity coverage',
            'Individual selected hashes verified; full original prefix verification is recorded by the earlier export',
            'Policy executable labels are not risk approval, fills, unique opportunities or profits'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();result=compare(args.evidence)
    with args.output.open('x',encoding='utf-8') as target:json.dump(result,target,indent=2)
    print('CLOCK_REPLAY=BASELINE_MATCHED; ORDERS=0; NETWORK_REQUESTS=0')
    print('UPLOAD_SUMMARY='+str(args.output))


if __name__=='__main__':main()
