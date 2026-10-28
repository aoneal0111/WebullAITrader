"""Bounded, append-only local research evidence. No order or provider methods."""
from collections import Counter, deque
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from queue import Queue, Empty, Full
from threading import Event, Lock, Thread
from time import monotonic
from uuid import UUID

MAX_BYTES=512*1024*1024
MAX_RECORDS=1_000_000
SEGMENT_BYTES=16*1024*1024
MAX_LINE=128*1024


def safe(value, depth=0):
    if depth>12:raise ValueError('EVIDENCE_DEPTH_EXCEEDED')
    if isinstance(value,Enum):return value.value
    if isinstance(value,(datetime,Decimal,UUID,Path)):return str(value)
    if isinstance(value,timedelta):return dict(total_seconds=value.total_seconds())
    if isinstance(value,(set,frozenset)):return sorted((safe(v,depth+1) for v in value),key=str)
    if is_dataclass(value):value={f.name:getattr(value,f.name) for f in fields(value)}
    if isinstance(value,dict):
        return {str(k):safe(v,depth+1) for k,v in value.items()
            if not any(s in str(k).lower() for s in ('secret','password','token','api_key','app_key','account_id','credential'))}
    if isinstance(value,(list,tuple)):
        if len(value)>2000:raise ValueError('EVIDENCE_COLLECTION_TOO_LARGE')
        return [safe(v,depth+1) for v in value]
    if value is None or type(value) in (str,int,float,bool):return value
    raise ValueError('UNSUPPORTED_EVIDENCE_TYPE')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


class DailyJournal:
    def __init__(self,directory,baseline,*,clock=lambda:datetime.now(UTC),timer=monotonic,
                 max_bytes=MAX_BYTES,max_records=MAX_RECORDS):
        if len(baseline)!=64 or any(c not in '0123456789abcdef' for c in baseline):raise ValueError('BASELINE_HASH_REQUIRED')
        if not 1<=max_bytes<=MAX_BYTES or not 1<=max_records<=MAX_RECORDS:raise ValueError('JOURNAL_BOUND_REQUIRED')
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=False)
        self.baseline,self.clock,self.timer=baseline,clock,timer
        self.max_bytes,self.max_records=max_bytes,max_records
        self._queue=Queue(maxsize=2048);self._stop=Event();self._lock=Lock()
        self._counts=Counter();self._recent=deque(maxlen=25);self._last='0'*64;self._bytes=0
        self._closed=False;self._failed=False;self._limited=False;self._dedup={}
        self._thread=Thread(target=self._write,name='atlas-daily-evidence',daemon=True);self._thread.start()

    def submit(self,kind,payload):
        # Only immutable owned runtime objects or caller-owned fresh mappings.
        # Serialization, hashing and disk I/O run on this journal's writer.
        try:
            with self._lock:
                if self._closed or self._failed or self._limited:return False
                try:self._queue.put_nowait((kind,payload,self.clock(),self.timer()))
                except Full:self._counts['queue_dropped']+=1;return False
                self._counts['queued']+=1;return True
        except Exception:
            with self._lock:self._counts['submit_failed']+=1
            return False

    def observe_market_event(self,event):
        try:
            from app.market_data.models import MarketEvent
            if isinstance(event,MarketEvent):self.submit('MARKET_EVENT',event)
        except Exception:
            with self._lock:self._counts['submit_failed']+=1

    def observe_paper_event(self,event):
        # Paper runtime event is captured before downstream projections, so
        # an intermediate fill cannot disappear between GUI refresh snapshots.
        self.submit('PAPER_EVENT',event)

    def status(self):
        with self._lock:
            counts=dict(self._counts)
            return dict(scope='DAILY_RESEARCH_JOURNAL_V1',baseline_sha256=self.baseline,
                updated_at=self.clock().isoformat(),counts=counts,bytes_written=self._bytes,
                chain_head=self._last,writer_failed=self._failed,capacity_reached=self._limited,
                closed=self._closed,writer_alive=self._thread.is_alive(),recent=list(self._recent),
                orders_dispatched=0,grok_requests=0,execution_authorized=False,
                evidence_complete=not self._failed and not self._limited and not counts.get('queue_dropped',0)
                    and not counts.get('submit_failed',0) and not counts.get('invalid_record',0),
                limitations=['Observed subscribed stream only; not whole-market history',
                    'Queue loss, capacity, or writer failure makes replay coverage incomplete',
                    'Recording does not qualify strategies or enable entries'])

    def _checkpoint(self):
        target=self.directory/'journal-status.json';tmp=target.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.status(),indent=2),encoding='utf-8');tmp.replace(target)

    def _write(self):
        stream=None;segment=0;segment_bytes=0;last_checkpoint=0
        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:kind,payload,received,tick=self._queue.get(timeout=.1)
                except Empty:
                    if self.timer()-last_checkpoint>=2:self._checkpoint();last_checkpoint=self.timer()
                    continue
                try:
                    payload=safe(payload)
                    # Repeated bar windows are common. Keep changes, bound dedup
                    # state, and explicitly count unchanged repeats.
                    if kind=='BARS':
                        key=payload['symbol'];fingerprint=sha256(canonical(payload['bars'])).hexdigest()
                        if self._dedup.get(key)==fingerprint:
                            with self._lock:self._counts['unchanged_bar_windows']+=1
                            continue
                        self._dedup[key]=fingerprint
                        if len(self._dedup)>256:self._dedup.pop(next(iter(self._dedup)))
                    with self._lock:sequence=self._counts['written']+1;previous=self._last
                    record=dict(schema='ATLAS_DAILY_EVIDENCE_V1',sequence=sequence,kind=kind,
                        received_at=received.isoformat(),received_monotonic=tick,
                        baseline_sha256=self.baseline,previous_sha256=previous,payload=payload)
                    record['sha256']=sha256(canonical(record)).hexdigest();line=canonical(record)+b'\n'
                    if len(line)>MAX_LINE:raise ValueError('RECORD_TOO_LARGE')
                except Exception:
                    with self._lock:self._counts['invalid_record']+=1
                    continue
                with self._lock:
                    if sequence>self.max_records or self._bytes+len(line)>self.max_bytes:
                        self._limited=True;self._counts['capacity_dropped']+=1;continue
                if stream is None or segment_bytes+len(line)>SEGMENT_BYTES:
                    if stream is not None:stream.close()
                    segment+=1;segment_bytes=0
                    stream=(self.directory/f'events-{segment:05d}.jsonl').open('xb')
                stream.write(line);stream.flush();segment_bytes+=len(line)
                with self._lock:
                    self._counts['written']+=1;self._counts[kind]+=1;self._bytes+=len(line);self._last=record['sha256']
                    self._recent.append(dict(sequence=sequence,kind=kind,received_at=record['received_at'],
                        sha256=record['sha256'],payload=payload if kind in ('ASSESSMENT','STREAM_ASSESSMENT') else {}))
                if self.timer()-last_checkpoint>=2:self._checkpoint();last_checkpoint=self.timer()
        except Exception:
            with self._lock:self._failed=True;self._counts['writer_failures']+=1
        finally:
            if stream is not None:stream.close()
            try:self._checkpoint()
            except Exception:
                with self._lock:self._failed=True

    def close(self):
        with self._lock:self._closed=True
        self._stop.set();self._thread.join(3)
        report=self.status();report['writer_stopped']=not self._thread.is_alive()
        if report['writer_stopped']:self._checkpoint()
        return report


def verify_journal(directory):
    """Streaming chain audit, including final receipt. Never executes records."""
    root=Path(directory);previous='0'*64;sequence=0;total=0;baseline=None
    for index,path in enumerate(sorted(root.glob('events-*.jsonl')),1):
        if path.name!=f'events-{index:05d}.jsonl' or path.is_symlink():raise ValueError('SEGMENT_SEQUENCE_OR_LINK')
        with path.open('rb') as stream:
            for line in stream:
                total+=len(line)
                if len(line)>MAX_LINE or total>MAX_BYTES or not line.endswith(b'\n'):raise ValueError('JOURNAL_BOUND_OR_PARTIAL_LINE')
                record=json.loads(line);claimed=record.pop('sha256')
                sequence+=1
                if (sequence>MAX_RECORDS or record['sequence']!=sequence or record['previous_sha256']!=previous
                        or sha256(canonical(record)).hexdigest()!=claimed):raise ValueError('JOURNAL_CHAIN_CHANGED')
                if baseline is None:baseline=record['baseline_sha256']
                if record['baseline_sha256']!=baseline:raise ValueError('BASELINE_CHANGED')
                previous=claimed
    receipt=json.loads((root/'journal-status.json').read_text())
    if (not receipt['closed'] or receipt['writer_failed'] or receipt['chain_head']!=previous
            or receipt['counts'].get('written',0)!=sequence or receipt['bytes_written']!=total):
        raise ValueError('FINAL_RECEIPT_MISMATCH_OR_NOT_CLOSED')
    return dict(records=sequence,bytes=total,chain_head=previous,baseline_sha256=receipt['baseline_sha256'],
        evidence_complete=receipt['evidence_complete'],execution_authorized=False)


class RecordingMarketObserver:
    """Preserve the native observer protocol, not just its call operator.

    Broker composition discovers start/stop, scanner binding, protection,
    history preload and retained-symbol methods dynamically with getattr.
    A function closure hides those methods and leaves engines uninitialized.
    """
    def __init__(self,native,journal):
        self._native=native
        self._journal=journal

    def __getattr__(self,name):
        return getattr(self._native,name)

    def __call__(self,event):
        try:self._journal.observe_market_event(event)
        except Exception:pass  # Optional evidence never changes native dispatch.
        if self._native is not None:return self._native(event)
        return None
