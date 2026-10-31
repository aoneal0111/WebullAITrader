"""Read-only index of known Atlas research receipts; no database opens."""
import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path

NAMES={'manifest.json','history-manifest.json','research-manifest.json','research-summary.json',
    'candidate-screen-summary.json','signal-comparison.json','full_research_final.json',
    'session-summary.json','summary.json','frozen-protocol.json'}


def inventory(repo,local_root=None):
    repo=Path(repo).resolve()
    local_root=Path(local_root) if local_root is not None else Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'AtlasResearch'
    roots=[repo/'data/research',local_root]
    records=[];visited=0;read_bytes=0;limited=False
    # Explicit depth and directory exclusions avoid traversing upstream clones,
    # venvs, review worktrees or raw price archives.
    excluded={'review-source','audit-source','frozen-source','pinned-upstream','pinned-candidates',
        'candles','checks','pinned-upstream','__pycache__','.git','.venv','private-config-backups'}
    for root in roots:
        if not root.is_dir():continue
        stack=[(root,0)]
        while stack:
            directory,depth=stack.pop()
            try:children=sorted(directory.iterdir(),key=lambda p:p.name)
            except OSError:continue
            for path in children:
                visited+=1
                if visited>20000:limited=True;break
                if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):continue
                if path.is_dir():
                    if depth<6 and path.name not in excluded:stack.append((path,depth+1))
                elif path.name in NAMES and path.is_file():
                    stat=path.stat();digest=None
                    if stat.st_size<=1_000_000 and read_bytes+stat.st_size<=16_000_000:
                        raw=path.read_bytes();read_bytes+=len(raw);digest=sha256(raw).hexdigest()
                    records.append(dict(path=str(path.resolve()),bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime,UTC).isoformat(),sha256=digest))
            if limited:break
        if limited:break
    return dict(scope='READ_ONLY_RESEARCH_HISTORY_INVENTORY_V1',created_at=datetime.now(UTC).isoformat(),
        inspected_entries=visited,truncated=limited,receipts=records,databases_opened=0,network_requests=0,
        limitations=['Receipt presence is not proof of complete or valid history',
            'Large files indexed by metadata only; no private performance values exported',
            'Historical episode/membership counts are not independently executed trades',
            'Unrecorded depth, tick availability, options chains and news cannot be reconstructed from OHLCV'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    report=inventory(args.repo)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    print('HISTORY_INVENTORY='+str(args.output.resolve()))


if __name__=='__main__':main()
