"""Read-only bounded snapshot-file adapter for the disconnected observer.

No configuration, environment, broker or provider construction. The caller owns
the observer's injected client/clock. No command here enables online inference.
"""
from dataclasses import fields
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

from .engine_catalog import EngineId
from .grok_observer import Snapshot,GrokObserverTrial


def load_snapshots(path,baseline_sha256):
    path=Path(path)
    # Bound actual reads even if a producer grows the file after stat().
    with path.open('rb') as handle:raw=handle.read(1_000_001)
    if len(raw)>1_000_000:raise ValueError('snapshot input exceeds byte limit')
    lines=raw.splitlines()
    if len(lines)>100:raise ValueError('at most100 snapshot records required')
    result=[];identities=set();last={}
    def unique(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('duplicate JSON field')
            out[k]=v
        return out
    for line in lines:
        if not line or len(line)>4096:raise ValueError('bounded nonempty snapshot lines required')
        row=json.loads(line,object_pairs_hook=unique)
        if not isinstance(row,dict) or set(row)!={f.name for f in fields(Snapshot)}:
            raise ValueError('exact snapshot fields required; arbitrary payloads are refused')
        row['engine']=EngineId(row['engine'])
        for k in ('bid','ask'):row[k]=Decimal(row[k])
        for k in ('bid_at','ask_at','captured_at'):row[k]=datetime.fromisoformat(row[k])
        snap=Snapshot(**row)
        if snap.baseline_sha256!=baseline_sha256:raise ValueError('baseline mismatch')
        if not snap.fresh(snap.captured_at):raise ValueError('recorded quote was stale or future dated at capture')
        identity=(snap.engine,snap.event_id)
        if identity in identities:raise ValueError('duplicate engine/event identity')
        if snap.engine in last and snap.captured_at<last[snap.engine]:raise ValueError('out-of-order engine snapshots')
        identities.add(identity);last[snap.engine]=snap.captured_at;result.append(snap)
    return tuple(result)


def observe_file(path,trial,baseline_sha256):
    if not isinstance(trial,GrokObserverTrial):raise ValueError('disconnected observer trial required')
    # Validate the entire bounded input before making even the first call.
    snapshots=load_snapshots(path,baseline_sha256)
    return tuple(trial.observe(snapshot) for snapshot in snapshots)
