"""Closed-journal blocker comparison; never a fill simulation or order path."""
import argparse
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from .daily_research_journal import canonical, verify_journal
from .scalper_evidence_audit import typed, reconstructed_preview, POLICY_FILES
from app.strategies.warrior_momentum.quick_scalper import (
    QuickScalpSnapshot, QuickScalperConfig, QuickScalperPolicy,
)

# Exact previous source whose arithmetic branch remains unchanged in this build.
# Other historical policies require their own reviewed replay implementation.
PRIOR_SOURCE = (
    '13696be266177ac0f0f6bce5b4e30ed78a80f941d52c0bcad38e375ff85b0e00',
    '41c5e7b215dbf2801b1aca406d5a047f29d08e88e3785010c4e8fb5933f951c1',
)


def compare(session):
    root = Path(session).resolve()
    receipt = verify_journal(root/'journal')
    manifest = json.loads((root/'research-manifest.json').read_text())
    summary = json.loads((root/'research-summary.json').read_text())
    source_root = Path(__file__).resolve().parents[2]
    current = tuple(sha256((source_root/p).read_bytes()).hexdigest() for p in POLICY_FILES)
    recorded = tuple(manifest.get('source_files', {}).get(p) for p in POLICY_FILES)
    if (receipt['evidence_complete'] is not True
            or recorded not in (PRIOR_SOURCE, current)
            or summary.get('policy_unchanged') is not True
            or manifest.get('scope') != 'NORMAL_DESKTOP_DAILY_RESEARCH_V1'
            or manifest.get('baseline_sha256') != receipt['baseline_sha256']):
        raise ValueError('REVIEWED_FROZEN_SOURCE_REQUIRED')
    baseline = candidate = None
    counts = {'stream': Counter(), 'confirmation': Counter()}
    revised = {'stream': Counter(), 'confirmation': Counter()}
    transitions = {'stream': Counter(), 'confirmation': Counter()}
    mismatch = 0
    sequence = 0
    previous = '0'*64
    for path in sorted((root/'journal').glob('events-*.jsonl')):
        with path.open('rb') as stream:
            for line in stream:
                row = json.loads(line)
                claimed = row.pop('sha256')
                sequence += 1
                if (row['sequence'] != sequence or row['previous_sha256'] != previous
                        or sha256(canonical(row)).hexdigest() != claimed):
                    raise ValueError('JOURNAL_CHANGED_DURING_REPLAY')
                previous = claimed
                p = row['payload']
                if row['kind'] == 'NATIVE_POLICY_BASELINE':
                    if baseline is not None:
                        raise ValueError('MULTIPLE_BASELINES')
                    raw = dict(p['settings']['scalper'])
                    if recorded == PRIOR_SOURCE and 'cent_fill_anchored_economics' in raw:
                        raise ValueError('PRIOR_POLICY_SCHEMA_CHANGED')
                    # Sole reviewed additive schema change, default OFF in V2.
                    raw.setdefault('cent_fill_anchored_economics', False)
                    config = typed(QuickScalperConfig, raw)
                    if not config.cent_pullback_support_enabled:
                        raise ValueError('CENT_BASELINE_REQUIRED')
                    baseline = QuickScalperPolicy(config)
                    candidate = QuickScalperPolicy(replace(config, cent_fill_anchored_economics=True))
                    continue
                kind = None
                if row['kind'] == 'STREAM_ASSESSMENT':
                    kind = 'stream'
                    snap = typed(QuickScalpSnapshot, p['snapshot'])
                elif row['kind'] == 'ASSESSMENT' and p.get('engine') == 'QUICK_SCALPER':
                    kind = 'confirmation'
                    from datetime import datetime
                    snap = reconstructed_preview(typed(QuickScalpSnapshot, p['preview']),
                        p['quote'], datetime.fromisoformat(p['assessed_at']))
                if kind is None:
                    continue
                if baseline is None:
                    raise ValueError('POLICY_BASELINE_MISSING')
                old, new = baseline.assess(snap), candidate.assess(snap)
                if kind == 'stream':
                    matches = old.reason == p['reason'] and old.decision.value == p['decision']
                else:
                    label = ('ENTRY_CANDIDATE' if old.decision.value == 'EXECUTABLE' else
                             'WAIT' if old.decision.value == 'WAIT' else 'BLOCKED')
                    matches = label == p['baseline_result']
                mismatch += not matches
                counts[kind][old.reason] += 1
                revised[kind][new.reason] += 1
                transitions[kind][old.reason+' -> '+new.reason] += 1
    if (baseline is None or previous != receipt['chain_head'] or sequence != receipt['records']):
        raise ValueError('JOURNAL_CHANGED_OR_BASELINE_MISSING')
    if mismatch:
        raise ValueError('BASELINE_REPLAY_MISMATCH')
    return dict(scope='OFFLINE_SCALPER_REPAIR_BLOCKER_COMPARISON_V1',
        baseline_replay_matches=True, evidence_complete=receipt['evidence_complete'],
        baseline_sha256=receipt['baseline_sha256'], journal_chain_head=receipt['chain_head'],
        recorded_source_sha256=recorded,
        implementation_sha256=current,
        baseline_reasons={k:dict(v) for k,v in counts.items()},
        fill_anchored_reasons={k:dict(v) for k,v in revised.items()},
        transitions={k:dict(v) for k,v in transitions.items()},
        network_requests=0, grok_requests=0, orders=0, promotion='NONE',
        limitations=[
            'Same frozen snapshots and provider times; cost-accounting comparison only.',
            'Plateau support requires original distinct stream updates; no missing trough is inferred.',
            'Repeated assessments are not unique opportunities or win-rate samples.',
            'Executable policy labels do not prove risk approval, fills, profit or remaining movement.',
            'Stale provider quotes remain stale; no entitlement or feed repair is claimed.',
            'No trial expiry, budget, settings, installed policy or position protection is changed.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = compare(args.session)
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str, allow_nan=False)
    print('REPAIR_REPLAY=COMPLETE; PROMOTION=NONE; ORDERS=0')
    print('UPLOAD_SUMMARY='+str(args.output.resolve()))


if __name__ == '__main__':
    main()
