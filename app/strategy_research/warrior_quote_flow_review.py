"""Read a closed source-bound journal; export compact Warrior quote-flow evidence."""
import argparse
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

MAX_BYTES=2*1024**3
MAX_RECORDS=2_000_000
MAX_LINE=128*1024


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def stamp(value):
    at=datetime.fromisoformat(value.replace('Z','+00:00'))
    if at.utcoffset() is None:raise ValueError('AWARE_TIME_REQUIRED')
    return at


def age(at,value):
    try:return round((at-stamp(value)).total_seconds(),6)
    except (AttributeError,TypeError,ValueError):return None


def bucket(value):
    if value is None:return 'MISSING'
    if value<0:return 'FUTURE'
    if value<=2:return 'WITHIN_2S'
    if value<=5:return 'BETWEEN_2_AND_5S'
    return 'OLDER_THAN_5S'


class Flow:
    def __init__(self):
        self.sources=defaultdict(Counter);self.market=Counter();self.samples=defaultdict(list)
        self.states=OrderedDict();self.per_symbol=OrderedDict()

    def consume(self,row):
        p=row['payload']
        if row['kind']=='MARKET_EVENT':
            if str(p.get('source','')).lower()!='webull':
                self.market['UNTRUSTED_STREAM_SKIPPED']+=1;return
            symbol=p.get('symbol');kind=p.get('event_type');payload=p.get('payload',{})
            if not symbol or kind not in ('QUOTE','TRADE'):return
            key='book' if kind=='QUOTE' else 'trade'
            retained=key=='trade' and payload.get('trade_id')=='snapshot-retained-price'
            self.market['QUOTE' if key=='book' else 'VOLUME_ONLY_SNAPSHOT' if retained else
                'PRICED_SNAPSHOT' if str(payload.get('trade_id','')).startswith('snapshot') else 'TICK']+=1
            state=self.states.setdefault(symbol,{})
            at=stamp(p['timestamp']);old=state.get(key)
            if old is None or at>=stamp(old['timestamp']):
                if not retained:state[key]=p
            if retained:self.market['BORROWED_PRICE_NOT_TRADE_EVIDENCE']+=1
            self.states.move_to_end(symbol)
            while len(self.states)>512:self.states.popitem(last=False)
            return
        if row['kind']!='ASSESSMENT' or p.get('engine')!='WARRIOR_MOMENTUM_V1':return
        at=stamp(p['assessed_at']);q=p.get('quote') or {};symbol=str(q.get('symbol') or 'UNKNOWN').strip().upper()
        source=p.get('quote_provenance') or 'UNAVAILABLE_OR_UNATTRIBUTED'
        if source not in ('WEBULL_STREAM','WEBULL_OPENAPI'):source='UNAVAILABLE_OR_UNATTRIBUTED'
        counts=self.sources[source];counts['ASSESSMENTS']+=1
        ages={part:age(at,q.get(part+'_timestamp')) for part in ('bid','ask','last')}
        for part,value in ages.items():counts[part.upper()+'_'+bucket(value)]+=1
        state=self.states.get(symbol,{})
        stream_ages={part:age(at,state.get(key,{}).get('timestamp'))
                     for part,key in (('book','book'),('trade','trade'))}
        fresh=all(value is not None and 0<=value<=2 for value in stream_ages.values())
        counts['RECORDED_STREAM_BOTH_WITHIN_2S' if fresh else 'RECORDED_STREAM_NOT_BOTH_WITHIN_2S']+=1
        for part,value in stream_ages.items():counts['RECORDED_'+part.upper()+'_'+bucket(value)]+=1
        self.per_symbol.setdefault(symbol,Counter())['ASSESSMENTS']+=1
        self.per_symbol.move_to_end(symbol)
        while len(self.per_symbol)>512:self.per_symbol.popitem(last=False)
        blocker=next((part.upper()+'_'+bucket(ages[part]) for part in ('bid','ask','last')
                      if ages[part] is None or not 0<=ages[part]<=2),'ALL_WITHIN_2S')
        self.per_symbol[symbol][blocker]+=1
        key=source+'|'+blocker
        if len(self.samples[key])<3:
            self.samples[key].append(dict(symbol=symbol,assessed_at=p['assessed_at'],
                offered_timestamps={part:q.get(part+'_timestamp') for part in ('bid','ask','last')},
                offered_age_seconds=ages,recorded_stream_age_seconds=stream_ages,
                baseline_result=p.get('baseline_result'),setup_state=p.get('setup_state')))

    def result(self):
        return dict(source_counts={k:dict(v) for k,v in self.sources.items()},
            market_event_counts=dict(self.market),samples=dict(self.samples),
            most_assessed_symbols=[dict(symbol=k,counts=dict(v)) for k,v in
                sorted(self.per_symbol.items(),key=lambda x:(-x[1]['ASSESSMENTS'],x[0]))[:20]])


def export(review,output,*,diagnostic=None,run_id=None,diagnostic_sha256=None):
    review=Path(review);output=Path(output)
    if review.is_symlink() or not review.is_file() or review.stat().st_size>64*1024**2:
        raise ValueError('BOUNDED_EXISTING_REVIEW_REQUIRED')
    if output.exists():raise ValueError('NEW_OUTPUT_REQUIRED')
    with ZipFile(review) as archive:
        def read(name):
            if archive.getinfo(name).file_size>8*1024**2:raise ValueError('REVIEW_METADATA_BOUND')
            return archive.read(name)
        report=json.loads(read('paper-day-review.json'))
        if report.get('scope')!='PRIVATE_CLOSED_PAPER_DAY_REVIEW_V1':raise ValueError('CLOSED_REVIEW_REQUIRED')
        manifest_bytes=read('capture/research-manifest.json');summary_bytes=read('capture/research-summary.json')
    session=Path(report['recording_session'])
    for name,raw in (('research-manifest.json',manifest_bytes),('research-summary.json',summary_bytes)):
        if (session/name).is_symlink() or (session/name).read_bytes()!=raw:raise ValueError('SOURCE_METADATA_CHANGED')
    summary=json.loads(summary_bytes);journal=summary['journal'];baseline=journal['baseline_sha256']
    if not journal.get('closed') or journal.get('writer_alive') or journal.get('writer_failed'):
        raise ValueError('CLOSED_STOPPED_WRITER_REQUIRED')
    manifest=json.loads(manifest_bytes)
    if manifest.get('baseline_sha256')!=baseline:raise ValueError('SOURCE_BASELINE_CHANGED')
    expected=journal['counts']['written'];expected_bytes=journal['bytes_written']
    if not 0<=expected<=MAX_RECORDS or not 0<=expected_bytes<=MAX_BYTES:raise ValueError('JOURNAL_REVIEW_BOUND')
    previous='0'*64;seen=total=0;flow=Flow()
    for index,path in enumerate(sorted((session/'journal').glob('events-*.jsonl')),1):
        if path.name!=f'events-{index:05d}.jsonl' or path.is_symlink():raise ValueError('REGULAR_ORDERED_SEGMENTS_REQUIRED')
        with path.open('rb') as stream:
            while line:=stream.readline(MAX_LINE+1):
                if len(line)>MAX_LINE or not line.endswith(b'\n'):raise ValueError('COMPLETE_BOUNDED_LINES_REQUIRED')
                seen+=1;total+=len(line)
                if seen>expected or total>expected_bytes:raise ValueError('SOURCE_EXCEEDS_CLOSED_REVIEW')
                row=json.loads(line);claimed=row['sha256'];unsigned={k:v for k,v in row.items() if k!='sha256'}
                if (row['sequence']!=seen or row['previous_sha256']!=previous or row['baseline_sha256']!=baseline
                        or sha256(canonical(unsigned)).hexdigest()!=claimed):raise ValueError('JOURNAL_CHAIN_CHANGED')
                previous=claimed;flow.consume(row)
                if seen%50000==0:print('QUOTE_FLOW_VERIFIED_RECORDS='+str(seen),flush=True)
    if seen!=expected or total!=expected_bytes or previous!=journal['chain_head']:raise ValueError('CLOSED_COVERAGE_MISMATCH')
    if (session/'research-summary.json').read_bytes()!=summary_bytes:raise ValueError('SOURCE_METADATA_CHANGED')
    diagnostic_bytes=None
    if diagnostic is not None:
        diagnostic=Path(diagnostic)
        if not run_id or not diagnostic_sha256 or diagnostic.is_symlink() or diagnostic.stat().st_size>16*1024**2:
            raise ValueError('EXACT_BOUNDED_DIAGNOSTIC_IDENTITY_REQUIRED')
        diagnostic_bytes=diagnostic.read_bytes();d=json.loads(diagnostic_bytes)
        if d.get('run_id')!=run_id or sha256(diagnostic_bytes).hexdigest()!=diagnostic_sha256.lower():
            raise ValueError('EXACT_DIAGNOSTIC_CHANGED')
        if (abs((stamp(d['captured_at'])-stamp(journal['updated_at'])).total_seconds())>60
                or stamp(d['process_started_at'])>stamp(manifest['started_at'])+timedelta(seconds=60)):
            raise ValueError('DIAGNOSTIC_RECORDING_WINDOW_MISMATCH')
    result=dict(scope='VERIFIED_CLOSED_WARRIOR_QUOTE_FLOW_V1',recording_session=str(session),
        review_sha256=sha256(review.read_bytes()).hexdigest(),verified_records=seen,verified_bytes=total,
        chain_head=previous,**flow.result(),orders=0,provider_requests=0,parameters_changed=False,
        limitations=['Recording coverage only; stream state is reconstructed in journal order with a 512-symbol bound',
            'Two-second categories compare unmodified provider times, not a complete entry authorization',
            'A cached quote does not prove current subscription membership; raw SDK bodies are not in this journal',
            'Repeated assessments are not independent opportunities or profitability samples'])
    with ZipFile(output,'x',compression=ZIP_DEFLATED) as archive:
        archive.writestr('warrior-quote-flow.json',json.dumps(result,indent=2))
        if diagnostic_bytes is not None:archive.writestr('exact-runtime.json',diagnostic_bytes)
    print('UPLOAD_FILE='+str(output),flush=True)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--diagnostic',type=Path);p.add_argument('--run-id');p.add_argument('--diagnostic-sha256')
    a=p.parse_args();export(a.review,a.output,diagnostic=a.diagnostic,run_id=a.run_id,diagnostic_sha256=a.diagnostic_sha256)


if __name__=='__main__':main()
