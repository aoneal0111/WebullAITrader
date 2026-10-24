"""Explicit one-way capture staging. No desktop hooks, model or execution imports."""
from collections import Counter
from dataclasses import asdict, fields
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from queue import Queue, Full, Empty
from threading import Event, Lock, Thread

from .engine_catalog import EngineId
from .grok_observer import Snapshot
from .grok_observer_context import ContextBar, StrategyContext

MAX_BYTES=1_000_000
MAX_RECORDS=100
MAX_LINE=24576


def _default(value):
    if isinstance(value,datetime):return value.isoformat()
    if isinstance(value,Decimal):return str(value)
    raise TypeError('Unsupported capture value')


def webull_quote_snapshot(*,engine,event_id,symbol,row,captured_at,baseline):
    """Decode supplied provider data with the canonical parser; never fetch data."""
    from app.strategies.warrior_momentum.execution_quote import parse_execution_quote
    quote=parse_execution_quote(symbol,row,confirmed_at=captured_at)
    if quote is None:raise ValueError('Canonical Webull quote unavailable')
    snapshot=Snapshot(engine,event_id,quote.symbol,quote.bid,quote.ask,quote.bid_timestamp,
        quote.ask_timestamp,captured_at,baseline,'WEBULL_OPENAPI')
    return snapshot,'WEBULL_PROVIDER_QUOTE_TIME_SHARED'


def _encode(snapshot,context,baseline,now,quote_basis=None):
    if not isinstance(snapshot,Snapshot) or not isinstance(context,StrategyContext):
        raise ValueError('Typed owned snapshot and context required')
    if snapshot.baseline_sha256!=baseline or not snapshot.fresh(now):
        raise ValueError('Frozen baseline and fresh side timestamps required')
    context.payload(snapshot)
    basis=quote_basis or ('FIXTURE_ONLY' if snapshot.source=='RECORDED_FIXTURE' else None)
    if not ((snapshot.source=='RECORDED_FIXTURE' and basis=='FIXTURE_ONLY') or
            (snapshot.source=='WEBULL_OPENAPI' and basis=='WEBULL_PROVIDER_QUOTE_TIME_SHARED'
             and snapshot.bid_at==snapshot.ask_at)):
        raise ValueError('Explicit supported provider timestamp basis required')
    row=dict(schema='ATLAS_OBSERVER_CONTEXT_CAPTURE_V1',snapshot=asdict(snapshot),
             strategy_context=asdict(context),quote_timestamp_basis=basis)
    data=(json.dumps(row,default=_default,sort_keys=True,allow_nan=False)+'\n').encode()
    if len(data)>MAX_LINE:raise ValueError('Bounded capture line required')
    return data


class ContextCapture:
    """Nonblocking queue handoff to a private file writer, explicitly constructed.

    No entry/exit callbacks or providers are accepted. This is not wired to Atlas.
    submit never propagates validation/storage exceptions into its caller.
    """
    def __init__(self,directory,baseline,*,clock):
        import re
        if not isinstance(baseline,str) or not re.fullmatch('[0-9a-f]{64}',baseline):
            raise ValueError('Frozen baseline fingerprint required')
        self._baseline,self._clock=baseline,clock
        self._queue=Queue(maxsize=16);self._stop=Event();self._lock=Lock()
        self._counts=Counter();self._owners=Counter();self._seen=set();self._bytes=0
        self._closed=False;self._failed=False
        self._directory=Path(directory);self._directory.mkdir(parents=True,exist_ok=False)
        self._thread=Thread(target=self._write,name='atlas-private-context-writer',daemon=True)
        self._thread.start()

    def submit(self,snapshot,context,*,quote_basis=None):
        try:
            data=_encode(snapshot,context,self._baseline,self._clock(),quote_basis)
            identity=(snapshot.engine.value,snapshot.event_id)
            with self._lock:
                if self._closed or self._failed:
                    self._counts['closed_or_failed']+=1;return False
                if identity in self._seen:
                    self._counts['duplicates']+=1;return False
                if self._counts['enqueued']>=MAX_RECORDS or self._bytes+len(data)>MAX_BYTES:
                    self._counts['limit_rejections']+=1;return False
                try:self._queue.put_nowait((snapshot.engine.value,data))
                except Full:self._counts['queue_full']+=1;return False
                self._seen.add(identity);self._bytes+=len(data);self._counts['enqueued']+=1
                return True
        except Exception:
            with self._lock:self._counts['invalid_inputs']+=1
            return False

    def _write(self):
        try:
            with (self._directory/'private-observer-context.jsonl').open('xb') as stream:
                while not self._stop.is_set() or not self._queue.empty():
                    try:owner,data=self._queue.get(timeout=.05)
                    except Empty:continue
                    stream.write(data);stream.flush()
                    with self._lock:
                        self._counts['written']+=1;self._owners[owner]+=1
        except Exception:
            with self._lock:self._failed=True;self._counts['writer_failures']+=1

    def close(self):
        # Call from lifecycle/shutdown management, never a protection callback.
        with self._lock:self._closed=True
        self._stop.set();self._thread.join(timeout=2)
        with self._lock:
            summary=dict(scope='LOCAL_OBSERVER_CONTEXT_CAPTURE_V1',orders=0,grok_requests=0,network_requests=0,
                online_observer_ready=False,desktop_wiring='NOT_ATTESTED',baseline_modified=False,
                writer_stopped=not self._thread.is_alive(),writer_failed=self._failed,
                counts=dict(self._counts),records_by_engine=dict(self._owners),bytes_enqueued=self._bytes)
        if not self._thread.is_alive():
            path=self._directory/'summary.json'
            if not path.exists():
                with path.open('x',encoding='utf-8') as stream:json.dump(summary,stream,indent=2)
        return summary


def _unique(items):
    out={}
    for k,v in items:
        if k in out:raise ValueError('Duplicate JSON field')
        out[k]=v
    return out


def load_capture(path,baseline):
    """Validate the whole bounded private file before any injected model call."""
    with Path(path).open('rb') as stream:raw=stream.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Capture exceeds byte bound')
    lines=raw.splitlines()
    if not 1<=len(lines)<=MAX_RECORDS:raise ValueError('Bounded nonempty capture required')
    results=[];seen=set();last={}
    for line in lines:
        if not line or len(line)>MAX_LINE:raise ValueError('Bounded capture line required')
        row=json.loads(line,object_pairs_hook=_unique)
        if not isinstance(row,dict) or set(row)!={'schema','snapshot','strategy_context','quote_timestamp_basis'} or row['schema']!='ATLAS_OBSERVER_CONTEXT_CAPTURE_V1':
            raise ValueError('Exact capture envelope required')
        s,c=row['snapshot'],row['strategy_context']
        if not isinstance(s,dict) or set(s)!={f.name for f in fields(Snapshot)}:raise ValueError('Exact snapshot required')
        if not isinstance(c,dict) or set(c)!={f.name for f in fields(StrategyContext)}:raise ValueError('Exact context required')
        s['engine']=EngineId(s['engine']);c['engine']=EngineId(c['engine'])
        for name in ('bid','ask'):
            if not isinstance(s[name],str):raise ValueError('Exact decimal strings required')
            s[name]=Decimal(s[name])
        for name in ('bid_at','ask_at','captured_at'):s[name]=datetime.fromisoformat(s[name])
        c['captured_at']=datetime.fromisoformat(c['captured_at'])
        if not isinstance(c['bars'],list) or not 5<=len(c['bars'])<=30:raise ValueError('Bounded bar list required')
        bars=[]
        for bar in c['bars']:
            if not isinstance(bar,dict) or set(bar)!={f.name for f in fields(ContextBar)}:raise ValueError('Exact bar evidence required')
            for name in ('closed_at','available_at'):bar[name]=datetime.fromisoformat(bar[name])
            for name in ('open','high','low','close','volume'):
                if not isinstance(bar[name],str):raise ValueError('Exact decimal strings required')
                bar[name]=Decimal(bar[name])
            bars.append(ContextBar(**bar))
        c['bars']=tuple(bars)
        snapshot,context=Snapshot(**s),StrategyContext(**c)
        _encode(snapshot,context,baseline,snapshot.captured_at,row['quote_timestamp_basis'])
        identity=(snapshot.engine,snapshot.event_id)
        if identity in seen or (snapshot.engine in last and snapshot.captured_at<last[snapshot.engine]):
            raise ValueError('Unique ordered owned records required')
        seen.add(identity);last[snapshot.engine]=snapshot.captured_at
        results.append((snapshot,context))
    return tuple(results)


def observe_capture(path,trial,baseline):
    from .grok_observer import GrokObserverTrial
    if not isinstance(trial,GrokObserverTrial):raise ValueError('Injected observer-only trial required')
    records=load_capture(path,baseline)
    # The observer rechecks current freshness; old recordings never get retimed.
    return tuple(trial.observe(s,strategy_context=c) for s,c in records)
