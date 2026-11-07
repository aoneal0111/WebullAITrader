"""Read-only, anchored journal-prefix export for offline crypto signal review.

A running journal stays open. The checkpoint fixes an already-written prefix;
new records after that checkpoint are deliberately excluded, never called lost.
"""
import argparse
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4
import zipfile

from app.asset_modules.daily_research_journal import canonical, MAX_LINE, MAX_BYTES, MAX_RECORDS

KEYS = ('e0', 'nasos', 'bb', 'crsi', 'wave', 'nfi')
METADATA = ('config.json', 'resolved-config.json', 'profile.json', 'paper-manifest.json',
            'resolved-universe.json', 'runtime-check.json', 'recording-session.json', 'engine-status.json')


def read_json(path, limit=2*1024**2):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>limit:raise ValueError('METADATA_BOUND_OR_LINK')
    raw=path.read_bytes()
    if len(raw)>limit:raise ValueError('METADATA_BOUND')
    return json.loads(raw),raw


def recording(work, selected=None):
    work=Path(work)
    pointer={'session':selected['recording_id']} if selected else read_json(work/'recording-session.json')[0]
    # Only a validated recording basename is resolved against this local worker.
    name=str(pointer['session']).replace('\\','/').rstrip('/').split('/')[-1]
    if not re.fullmatch(r'recording-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}',name):
        raise ValueError('RECORDING_POINTER_REQUIRED')
    root=work/name
    if root.is_symlink() or not root.is_dir():raise ValueError('LOCAL_RECORDING_MISSING')
    if selected:
        anchor=selected['capture'];raw=(json.dumps(anchor,indent=2)+'\n').encode()
    else:
        engine,_=read_json(work/'engine-status.json')
        if engine.get('recording_id') not in (None,name):raise ValueError('RECORDING_TRANSITION_RETRY')
        anchor,raw=read_json(root/'journal-status.json')
    baseline=anchor.get('baseline_sha256','')
    if not re.fullmatch('[0-9a-f]{64}',baseline):raise ValueError('BASELINE_REQUIRED')
    config=work/('resolved-config.json' if (work/'resolved-config.json').exists() else 'config.json')
    _,config_raw=read_json(config)
    if sha256(config_raw).hexdigest()!=baseline:raise ValueError('RECORDED_CONFIG_CHANGED')
    return root,anchor,raw


def archive_prefix(archive, key, root, anchor):
    """Stream and verify exactly the checkpoint's records, ignoring append tail."""
    wanted=anchor['counts'].get('written',0);expected_bytes=anchor['bytes_written']
    if (type(wanted) is not int or not 0<=wanted<=MAX_RECORDS
            or type(expected_bytes) is not int or not 0<=expected_bytes<=MAX_BYTES):
        raise ValueError('JOURNAL_BOUND')
    previous='0'*64;sequence=0;total=0;counts=Counter();segments=[]
    for index,path in enumerate(sorted(root.glob('events-*.jsonl')),1):
        if sequence==wanted:break
        if path.name!=f'events-{index:05d}.jsonl' or path.is_symlink():
            raise ValueError('SEGMENT_SEQUENCE_OR_LINK')
        h=sha256();size=0
        with path.open('rb') as source, archive.open(key+'/journal/'+path.name,'w',force_zip64=True) as target:
            while sequence<wanted:
                line=source.readline(MAX_LINE+1)
                if not line:break
                if len(line)>MAX_LINE or not line.endswith(b'\n'):raise ValueError('PARTIAL_OR_OVERSIZE_RECORD')
                row=json.loads(line);claimed=row.pop('sha256');sequence+=1;total+=len(line)
                if (total>MAX_BYTES or row['schema']!='ATLAS_DAILY_EVIDENCE_V1'
                        or row['sequence']!=sequence or row['previous_sha256']!=previous
                        or row['baseline_sha256']!=anchor['baseline_sha256']
                        or sha256(canonical(row)).hexdigest()!=claimed):
                    raise ValueError('JOURNAL_CHAIN_CHANGED')
                previous=claimed;counts[row['kind']]+=1;target.write(line);h.update(line);size+=len(line)
        segments.append(dict(name=path.name,bytes=size,sha256=h.hexdigest()))
    if sequence!=wanted or total!=expected_bytes or previous!=anchor['chain_head']:
        raise ValueError('CHECKPOINT_PREFIX_MISMATCH')
    return dict(records=sequence,bytes=total,chain_head=previous,baseline_sha256=anchor['baseline_sha256'],
        kinds=dict(counts),segments=segments,checkpoint_at=anchor['updated_at'],
        source_recording_closed=anchor.get('closed',False),
        source_evidence_complete=anchor.get('evidence_complete',False),
        source_writer_failed=anchor.get('writer_failed',False),source_capacity_reached=anchor.get('capacity_reached',False),
        coverage='VERIFIED_WRITTEN_CHECKPOINT_PREFIX',execution_authorized=False)


def export(root, output=None, review=None):
    root=Path(root).resolve();fleet,fleet_raw=read_json(root/'fleet.json')
    if (fleet.get('scope')!='FIVE_INDEPENDENT_CRYPTO_PAPER_EXPERIMENTS_V1'
            or fleet.get('live_trading_enabled') is not False or not fleet.get('nfi_work')):
        raise ValueError('PAPER_FLEET_REQUIRED')
    if any((root/key).is_symlink() for key in KEYS[:-1]):raise ValueError('PROFILE_LINK')
    reviewed={};review_files={};review_hash=None
    if review:
        review=Path(review)
        if review.is_symlink() or review.stat().st_size>128*1024**2:raise ValueError('REVIEW_BOUND_OR_LINK')
        review_hash=sha256(review.read_bytes()).hexdigest()
        with zipfile.ZipFile(review) as prior:
            names=prior.namelist()
            if len(names)!=len(set(names)):raise ValueError('DUPLICATE_REVIEW_ENTRIES')
            def read_prior(name):
                if prior.getinfo(name).file_size>2*1024**2:raise ValueError('REVIEW_METADATA_BOUND')
                return prior.read(name)
            if json.loads(read_prior('fleet.json'))['id']!=fleet['id']:raise ValueError('REVIEW_FLEET_MISMATCH')
            for key in KEYS:
                reviewed[key]=json.loads(read_prior(key+'/engine-status.json'))
                for name in METADATA:
                    path=key+'/'+name
                    if path in names:review_files[path]=read_prior(path)

    output=Path(output or root/('signal-evidence-'+uuid4().hex+'.zip')).resolve()
    if output.exists():raise ValueError('OUTPUT_ALREADY_EXISTS')
    temporary=output.with_name(output.name+'.partial-'+uuid4().hex)
    report=dict(scope='CRYPTO_SIX_PROFILE_SIGNAL_EVIDENCE_PREFIX_V1',
        created_at=datetime.now(UTC).isoformat(),fleet_id=fleet['id'],profiles={},
        review_sha256=review_hash,anchor_origin='REVIEW_CHECKPOINTS' if review else 'CURRENT_JOURNAL_CHECKPOINTS',
        market_data_requests=0,grok_requests=0,orders=0,runtime_modified=False,
        limitations=['Each worker has its own checkpoint time; not simultaneous snapshots.',
            'Open journals are verified prefixes, not closed full-run chain audits.',
            'Coverage and source completeness are reported separately; missing source rows are never fabricated.',
            'Contains private candle evidence and strategy files; no trade performance qualification.'])
    try:
        with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
            archive.writestr('fleet.json',fleet_raw)
            for key in KEYS:
                work=Path(fleet['nfi_work']).resolve() if key=='nfi' else root/key
                rec,anchor,anchor_raw=recording(work,reviewed.get(key))
                print('VERIFYING_SIGNAL_EVIDENCE='+key+'; RECORDS='+str(anchor['counts'].get('written',0)),flush=True)
                result=archive_prefix(archive,key,rec,anchor)
                archive.writestr(key+'/checkpoint-anchor.json',anchor_raw)
                files={}
                paths=[work/name for name in METADATA if (work/name).is_file()
                       and (not review or name!='recording-session.json')]
                for folder in ('strategies','upstream'):
                    base=work/folder
                    if base.is_symlink():raise ValueError('SOURCE_DIRECTORY_LINK')
                    paths.extend(sorted(base.glob('*.py')))
                if len(paths)>100:raise ValueError('SOURCE_FILE_BOUND')
                for path in paths:
                    if path.is_symlink() or path.stat().st_size>5*1024**2:raise ValueError('SOURCE_BOUND_OR_LINK')
                    relative=path.relative_to(work).as_posix()
                    data=review_files.get(key+'/'+relative,path.read_bytes())
                    if relative==('resolved-config.json' if key=='nfi' else 'config.json'):
                        if sha256(data).hexdigest()!=anchor['baseline_sha256']:
                            raise ValueError('CONFIG_CHANGED_DURING_EXPORT')
                    archive.writestr(key+'/'+relative,data);files[relative]=sha256(data).hexdigest()
                result.update(recording_id=rec.name,files=files)
                report['profiles'][key]=result
            archive.writestr('evidence-manifest.json',json.dumps(report,indent=2)+'\n')
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print('UPLOAD_SIGNAL_EVIDENCE='+str(output),flush=True)
    print('READ_ONLY_EXPORT; WORKERS_LEFT_RUNNING; NO_API_REQUESTS; NO_SETTINGS_OR_RULE_CHANGES',flush=True)
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fleet',type=Path,required=True);parser.add_argument('--output',type=Path)
    parser.add_argument('--review',type=Path,help='Pin the exact uploaded review checkpoints, even after worker restarts')
    args=parser.parse_args();export(args.fleet,args.output,review=args.review)


if __name__=='__main__':main()
