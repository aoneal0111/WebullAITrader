"""Export bounded, private stopped-runtime evidence without starting Atlas."""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import zipfile


def export(trial, performance, output):
    trial, performance, output=map(Path,(trial,performance,output))
    pointer=json.loads((trial/'recording-session.json').read_text(encoding='utf-8-sig'))
    session=Path(pointer['session'])
    manifest=json.loads((session/'research-manifest.json').read_text(encoding='utf-8-sig'))
    if not (session/'research-summary.json').is_file():
        raise ValueError('CLOSE_ATLAS_BEFORE_EXPORT')
    started=datetime.fromisoformat(manifest['started_at'])
    output.mkdir(parents=True,exist_ok=False)
    for folder,base,names in (
        ('trial',trial,('experiment.json','recording-session.json','warrior-control-status.json')),
        ('session',session,('research-manifest.json','research-summary.json','engine-readiness.json')),
        ('journal',session/'journal',('journal-status.json',)),
    ):
        destination=output/folder;destination.mkdir()
        for name in names:
            path=base/name
            if path.is_file() and path.stat().st_size<=16*1024**2:
                shutil.copyfile(path,destination/name)
    failures=[];copied=[]
    # Process starts are matched to the recording; a later unrelated run must
    # not replace the earlier failed runtime merely because it is newest.
    candidates=sorted(performance.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:32]
    (output/'performance').mkdir()
    for path in candidates:
        if path.stat().st_size>16*1024**2:continue
        try:
            item=json.loads(path.read_text(encoding='utf-8-sig'))
            began=datetime.fromisoformat(item['process_started_at'])
            if not started-timedelta(minutes=3)<=began<=started+timedelta(minutes=3):continue
        except (ValueError,KeyError,TypeError):continue
        shutil.copyfile(path,output/'performance'/path.name);copied.append(path.name)
        for event in item.get('shutdown',{}).get('events',[]):
            if event.get('failure_present') or event.get('event_type')=='UNEXPECTED_CONSUMER_TERMINATION':
                failures.append(event)
        detail=item.get('metrics',{}).get('authoritative_failure')
        if detail:failures.append(dict(persisted_exception=detail))
    journals=sorted((session/'journal').glob('events-*.jsonl'))
    journal_bytes=sum(p.stat().st_size for p in journals)
    full_journal=journal_bytes<=256*1024**2
    if full_journal:
        for path in journals:shutil.copyfile(path,output/'journal'/path.name)
    report=dict(scope='STOPPED_RUNTIME_PRIVATE_REVIEW',performance_files=copied,
        failure_events=failures,original_exception_recovered=any('persisted_exception' in e for e in failures),
        full_journal_included=full_journal,journal_bytes=journal_bytes,
        limitations=['Older checkpoints may contain only AuthoritativeLaneFailure, not its underlying exception.',
            'No missing traceback is reconstructed or guessed. No runtime is restarted.'],
        orders=0,network_requests=0)
    (output/'review.json').write_text(json.dumps(report,indent=2))
    archive=output.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for path in output.rglob('*'):
            if path.is_file():z.write(path,path.relative_to(output).as_posix())
    return archive


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trial',type=Path,required=True);p.add_argument('--performance',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print('UPLOAD_EQUITY_REVIEW='+str(export(a.trial,a.performance,a.output)))


if __name__=='__main__':main()
