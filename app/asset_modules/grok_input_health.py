"""Bounded read-only live journal symbol discovery and observer wait diagnostics.

Symbol identity can drive news research; no journal price authorizes a review.
A record's hash is checked, but this bounded live tail is not a closed chain audit.
"""
from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

from .daily_research_journal import canonical
from .grok_observer_capture import decode_capture, MAX_LINE


class JournalSymbols:
    def __init__(self, session, baseline):
        self.root, self.baseline = Path(session) / 'journal', baseline
        self.path=None;self.offset=0;self.buffer=b'';self.skip_first=False
        self.counts=Counter()

    def poll(self, now):
        files=sorted(self.root.glob('events-*.jsonl'))
        if not files:return ()
        if len(files)>512:raise ValueError('JOURNAL_SEGMENT_BOUND')
        path=files[-1]
        if path.is_symlink() or not re.fullmatch(r'events-[0-9]{5}\.jsonl',path.name):
            raise ValueError('REGULAR_NATIVE_JOURNAL_SEGMENT_REQUIRED')
        size=path.stat().st_size
        if path!=self.path:
            self.path=path;self.offset=max(0,size-1024*1024);self.buffer=b'';self.skip_first=self.offset>0
        if size<self.offset:raise ValueError('NATIVE_JOURNAL_TRUNCATED')
        with path.open('rb') as stream:
            stream.seek(self.offset);chunk=stream.read(256*1024)
        self.offset+=len(chunk);self.buffer+=chunk
        self.counts['bytes_read']+=len(chunk)
        symbols=set()
        while b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1)
            if self.skip_first:self.skip_first=False;continue
            if len(line)>128*1024:
                self.counts['oversized_rows']+=1;continue
            try:
                row=json.loads(line)
                identity=row.pop('sha256')
                if (row['schema']!='ATLAS_DAILY_EVIDENCE_V1' or row['baseline_sha256']!=self.baseline
                    or sha256(canonical(row)).hexdigest()!=identity):
                    self.counts['invalid_identity']+=1;continue
                at=datetime.fromisoformat(row['received_at'])
                if not now-timedelta(minutes=15)<=at<=now:
                    self.counts['outside_recent_window']+=1;continue
                if row['kind'] not in {'MARKET_EVENT','MARKET_ELIGIBILITY','BARS','ASSESSMENT','STREAM_ASSESSMENT'}:continue
                p=row['payload']
                symbol=p.get('symbol')
                if row['kind']=='ASSESSMENT':symbol=p.get('quote',{}).get('symbol')
                if row['kind']=='STREAM_ASSESSMENT':symbol=p.get('snapshot',{}).get('symbol')
                if isinstance(symbol,str) and re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}',symbol):
                    symbols.add(symbol);self.counts['observed_equity_rows']+=1
            except (KeyError,ValueError,TypeError,AttributeError):self.counts['invalid_rows']+=1
        if len(self.buffer)>128*1024:raise ValueError('JOURNAL_LINE_BOUND')
        return tuple(sorted(symbols)[:24])


def input_health(session, baseline, now, *, cases=0,pending=0,counters=None):
    session=Path(session)
    readiness_path=session/'engine-readiness.json'
    tap={};checkpoint='AVAILABLE'
    try:
        if readiness_path.is_file() and readiness_path.stat().st_size<=65536:
            tap=json.loads(readiness_path.read_text()).get('tap_counts',{})
        else:checkpoint='UNAVAILABLE'
    except (OSError,ValueError,TypeError):checkpoint='UPDATING_OR_UNAVAILABLE'
    cached={}
    for engine in ('WARRIOR_MOMENTUM_V1','QUICK_SCALPER'):
        path=session/'private-capture'/('latest-'+engine+'.jsonl')
        if not path.is_file():cached[engine]={'status':'NO_CONTEXT_FILE'};continue
        try:
            with path.open('rb') as stream:raw=stream.read(MAX_LINE+1)
            if len(raw)>MAX_LINE:raise ValueError('BOUND')
            s,_=decode_capture(raw,baseline)[0]
            if s.engine.value!=engine or s.source not in {'WEBULL_OPENAPI','WEBULL_STREAM'}:raise ValueError('SOURCE')
            cached[engine]={'status':'FRESH' if s.fresh(now) else 'STALE_OR_FUTURE',
                'capture_age_seconds':round((now-s.captured_at).total_seconds(),3),
                'bid_age_seconds':round((now-s.bid_at).total_seconds(),3),
                'ask_age_seconds':round((now-s.ask_at).total_seconds(),3)}
        except (OSError,ValueError,KeyError,TypeError):cached[engine]={'status':'INVALID_CONTEXT'}
    if pending:reason='REVIEW_IN_FLIGHT'
    elif cases:reason='WAITING_FOR_NEXT_ENGINE_HOUR_OR_FRESH_CONTEXT'
    elif tap.get('captured',0):reason='WAITING_FOR_FRESH_NATIVE_CONTEXT'
    elif tap.get('stale_quote_at_capture',0):reason='NATIVE_QUOTES_STALE'
    elif tap.get('missing_shared_market_history',0):reason='NATIVE_BAR_HISTORY_MISSING'
    elif tap.get('unsupported_quote_provenance',0):reason='NATIVE_QUOTE_PROVENANCE_REFUSED'
    elif tap.get('invalid_native_evidence',0):reason='NATIVE_CONTEXT_CONSTRUCTION_REFUSED'
    else:reason='NO_FRESH_NATIVE_CONTEXT_AVAILABLE'
    return {'scope':'GROK_OBSERVER_INPUT_HEALTH_V1','updated_at':now.isoformat(),'waiting_reason':reason,
        'native_checkpoint':checkpoint,'native_tap_counts':tap,'latest_contexts':cached,'observer_counters':dict(counters or {}),
        'cases_scheduled':cases,'pending_cases':pending,'execution_authorized':False,'orders':0}


def save_health(output, report):
    path=Path(output)/'input-health.json'
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');temporary.replace(path)


def main():
    import argparse
    from datetime import UTC
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trial',required=True,type=Path)
    trial=p.parse_args().trial
    spec=json.loads((trial/'experiment.json').read_text())
    pointer=json.loads((trial/'recording-session.json').read_text())
    if pointer['trial_id']!=spec['trial_id']:raise ValueError('TRIAL_POINTER_MISMATCH')
    session=Path(pointer['session']);manifest=json.loads((session/'research-manifest.json').read_text())
    if manifest.get('paper_experiment')!=spec:raise ValueError('NATIVE_MANIFEST_TRIAL_MISMATCH')
    report=input_health(session,manifest['baseline_sha256'],datetime.now(UTC))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
