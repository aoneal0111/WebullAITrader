"""Read-only targeted evidence from a previously verified equity journal prefix.

No trading or parameter changes. The saved review fixes source identity and byte
coverage; all reread prefix bytes must still match its segment hashes.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4
import zipfile

MAX_REVIEW = 64*1024**2
MAX_SELECTED = 24*1024**2
MAX_RECORDS = 25000
MAX_LINE = 128*1024


def at(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('AWARE_EVIDENCE_TIME_REQUIRED')
    return result


def export(review, output):
    review,output=Path(review),Path(output)
    if review.is_symlink() or not review.is_file() or review.stat().st_size>MAX_REVIEW:
        raise ValueError('BOUNDED_EXISTING_REVIEW_REQUIRED')
    if output.exists():raise ValueError('NEW_OUTPUT_REQUIRED')
    with zipfile.ZipFile(review) as prior:
        def metadata(name):
            if prior.getinfo(name).file_size>8*1024**2:raise ValueError('REVIEW_METADATA_BOUND')
            return prior.read(name)
        report=json.loads(metadata('paper-day-review.json'))
        if report.get('scope')!='PRIVATE_INTERRUPTED_PAPER_SESSION_REVIEW_V1':
            raise ValueError('VERIFIED_INTERRUPTED_REVIEW_REQUIRED')
        prefix=report['verified_prefix'];session=Path(report['recording_session'])
        if prefix.get('scope')!='INTERRUPTED_SESSION_WRITTEN_PREFIX_REVIEW_V1':
            raise ValueError('VERIFIED_PREFIX_REQUIRED')
        manifest=metadata('capture/research-manifest.json')
        expected=report['files']['capture/research-manifest.json']['sha256']
        if sha256(manifest).hexdigest()!=expected or sha256((session/'research-manifest.json').read_bytes()).hexdigest()!=expected:
            raise ValueError('REVIEW_SOURCE_MANIFEST_CHANGED')
        native=report['native_trial']
        if native.get('status')=='RECONCILIATION_FAILED':raise ValueError('RECONCILED_REVIEW_REQUIRED')
        windows=[]
        for item in native['outcomes']:
            symbol=str(item.get('instrument','')).strip().upper()
            if not symbol:raise ValueError('TRADE_SYMBOL_REQUIRED')
            windows.append((symbol,at(item['opened_at'])-timedelta(seconds=60),
                at(item['closed_at'])+timedelta(seconds=60)))
        if not windows:raise ValueError('FILLED_TRADE_WINDOWS_REQUIRED')
        reasons=Counter();decisions=Counter();freshness=Counter();selected=Counter();excluded=Counter()
        traded_symbols={symbol for symbol,_,_ in windows}
        seen=0;size=0;records=0;previous='0'*64;total=0
        temporary=output.with_name(output.name+'.partial-'+uuid4().hex)
        try:
            with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_DEFLATED) as archive:
                with archive.open('selected-events.jsonl','w') as target:
                    for index,segment in enumerate(prefix['segments'],1):
                        name=segment['name']
                        if name!=f'events-{index:05d}.jsonl':raise ValueError('SEGMENT_SEQUENCE_REQUIRED')
                        path=session/'journal'/name
                        if path.is_symlink():raise ValueError('REGULAR_JOURNAL_REQUIRED')
                        remaining=segment['bytes'];h=sha256()
                        if type(remaining) is not int or not 0<=remaining<=32*1024**2:
                            raise ValueError('SEGMENT_BOUND')
                        with path.open('rb') as stream:
                            while remaining:
                                line=stream.readline(min(MAX_LINE+1,remaining))
                                if not line or len(line)>MAX_LINE or not line.endswith(b'\n'):
                                    raise ValueError('PREFIX_PARTIAL_OR_OVERSIZE_LINE')
                                remaining-=len(line);h.update(line);total+=len(line);seen+=1
                                row=json.loads(line);claimed=row['sha256']
                                unsigned={k:v for k,v in row.items() if k!='sha256'}
                                canonical=json.dumps(unsigned,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
                                if (row['sequence']!=seen or row['previous_sha256']!=previous or
                                        row['baseline_sha256']!=prefix['baseline_sha256'] or
                                        sha256(canonical).hexdigest()!=claimed):
                                    raise ValueError('PREFIX_CHAIN_CHANGED')
                                previous=claimed
                                kind=row['kind'];p=row['payload']
                                if kind=='STREAM_ASSESSMENT':
                                    reasons[str(p.get('reason','UNKNOWN'))]+=1
                                    decisions[str(p.get('decision','UNKNOWN'))]+=1
                                    if p.get('reason')=='PROVIDER_DATA_STALE':
                                        snap=p.get('snapshot',{})
                                        for component in ('last','bid','ask'):
                                            value=snap.get(component+'_timestamp');now=snap.get('decision_at')
                                            if value and now:
                                                age=(at(now)-at(value)).total_seconds()
                                                freshness[component+('_FUTURE' if age<0 else '_NONFUTURE')]+=1
                                received=at(row['received_at'])
                                symbol=str(p.get('symbol') or p.get('snapshot',{}).get('symbol') or '').strip().upper()
                                near=any(symbol==trade_symbol and start<=received<=end
                                    for trade_symbol,start,end in windows)
                                # Eligibility snapshots contain whole-market state, not the quote
                                # sequence. Retaining them per event overwhelmed compact exports.
                                keep=(kind in {'ASSESSMENT','SCALPER_CONFIRMATION_DIAGNOSTIC','READINESS_CHECK_FAILED'}
                                    or kind=='PAPER_EVENT' and symbol in traded_symbols
                                    or near and kind in {'STREAM_ASSESSMENT','MARKET_EVENT'})
                                if not keep:excluded[kind]+=1
                                if keep:
                                    size+=len(line);records+=1
                                    if size>MAX_SELECTED or records>MAX_RECORDS:raise ValueError('SELECTED_EVIDENCE_BOUND')
                                    target.write(line);selected[kind]+=1
                        if h.hexdigest()!=segment['sha256']:raise ValueError('REVIEW_PREFIX_SEGMENT_CHANGED')
                        print('ENTRY_EVIDENCE_VERIFIED_RECORDS='+str(seen),flush=True)
                if seen!=prefix['records'] or total!=prefix['bytes'] or previous!=prefix['chain_head']:
                    raise ValueError('REVIEW_PREFIX_COVERAGE_CHANGED')
                result=dict(scope='EQUITY_ENTRY_AND_STOP_DIAGNOSTIC_EVIDENCE_V1',trial_id=report['trial_id'],
                    review_sha256=sha256(review.read_bytes()).hexdigest(),verified_records=seen,
                    selected_records=records,selected_bytes=size,selected_kinds=dict(selected),
                    excluded_kinds=dict(excluded),selection_version='TRADE_SYMBOL_WINDOWS_V2',
                    stream_reasons=dict(reasons),stream_decisions=dict(decisions),
                    stale_component_classification=dict(freshness),
                    trade_windows=[dict(symbol=symbol,start=s.isoformat(),end=e.isoformat()) for symbol,s,e in windows],
                    orders=0,network_requests=0,parameters_changed=False,
                    limitations=['Verified checkpoint prefix only; later journal bytes excluded',
                        'Whole-market eligibility snapshots and unrelated-symbol events deliberately excluded; excluded counts reported',
                        'Stale component classification distinguishes negative age only, not full freshness approval',
                        'Repeated assessments are not independent opportunities or win-rate samples',
                        'Trade windows use received time; provider and processing times remain unmodified',
                        'Selected evidence supports debugging, not an independent profitability backtest'])
                archive.writestr('entry-evidence-summary.json',json.dumps(result,indent=2)+'\n')
                archive.writestr('original-paper-day-review.json',json.dumps(report,indent=2)+'\n')
                archive.writestr('research-manifest.json',manifest)
            if temporary.stat().st_size>8*1024**2:raise ValueError('COMPACT_ARCHIVE_BOUND')
            temporary.rename(output)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    print('UPLOAD_FILE='+str(output),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();export(args.review,args.output)


if __name__=='__main__':main()
