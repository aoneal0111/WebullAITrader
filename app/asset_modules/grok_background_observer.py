"""Two bounded advisory lanes, disconnected from native execution.

Ingestion continues while either model is busy. Only the latest pending context
per owner survives; no backlog of old quotes is turned into a model request.
"""
from collections import Counter, deque
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from .engine_catalog import EngineId
from .grok import GrokClient
from .grok_observer import GrokObserverTrial, Snapshot
from .grok_observer_context import StrategyContext
from .grok_observer_capture import MAX_BYTES, decode_capture

OWNERS=(EngineId.WARRIOR,EngineId.SCALPER)


class BackgroundAdvisers:
    def __init__(self,client,baseline,*,clock=lambda:datetime.now(UTC),timer=monotonic):
        if not isinstance(client,GrokClient):raise TypeError('Bounded shared client required')
        self.client,self.baseline,self.clock,self.timer=client,baseline,clock,timer
        self._trials={e:GrokObserverTrial(client,baseline,clock=clock) for e in OWNERS}
        self._lock=Lock();self._stopped=False
        self._pending={};self._active={};self._next={};self._latest={};self._completed=deque(maxlen=20)
        self._submitted=0;self._scheduling=Counter()

    def offer(self,snapshot,context):
        if (not isinstance(snapshot,Snapshot) or snapshot.engine not in OWNERS
                or snapshot.source!='WEBULL_OPENAPI' or snapshot.baseline_sha256!=self.baseline
                or not isinstance(context,StrategyContext)):
            raise ValueError('Owned current native context required')
        context.payload(snapshot)  # Identity, completed-bar causality and bounds.
        with self._lock:
            if self._stopped:return 'STOPPED'
            if not snapshot.fresh(self.clock()):status='STALE_OR_FUTURE_QUOTE'
            elif self._submitted>=20:status='REQUEST_LIMIT'
            elif snapshot.captured_at<=self._latest.get(snapshot.engine,datetime.min.replace(tzinfo=UTC)):
                status='OLDER_OR_EQUAL_CONTEXT'
            else:
                previous=self._pending.get(snapshot.engine)
                if previous is not None and snapshot.captured_at<=previous[0].captured_at:
                    status='OLDER_OR_EQUAL_CONTEXT'
                else:
                    status='LATEST_REPLACED' if previous is not None else 'PENDING'
                    self._pending[snapshot.engine]=(snapshot,context)
                    self._latest[snapshot.engine]=snapshot.captured_at
            self._scheduling[status]+=1
            return status

    def pump(self):
        with self._lock:
            if self._stopped:return
            for engine in OWNERS:
                if self._submitted>=20:break
                if engine in self._active or self.timer()<self._next.get(engine,0):continue
                item=self._pending.pop(engine,None)
                if item is None:continue
                if not item[0].fresh(self.clock()):
                    self._scheduling['STALE_BEFORE_REQUEST']+=1;continue
                self._submitted+=1;self._next[engine]=self.timer()+60
                thread=Thread(target=self._review,args=(engine,item),daemon=True,
                              name='GrokObserver-'+engine.name)
                self._active[engine]=thread
                thread.start()

    def _review(self,engine,item):
        with self._lock:
            if self._stopped:
                self._active.pop(engine,None);return
        try:record=self._trials[engine].observe(item[0],strategy_context=item[1])
        except Exception:record=None
        with self._lock:
            self._active.pop(engine,None)
            if record is not None:self._completed.append(record)
            else:self._scheduling['LOCAL_REVIEW_FAILED']+=1

    def drain(self):
        with self._lock:
            records=tuple(self._completed);self._completed.clear();return records

    def stop(self):
        # Does not wait on a provider and never interrupts local protection.
        with self._lock:
            self._stopped=True;self._pending.clear()

    def finish(self,grace=31):
        if not 0<=grace<=31:raise ValueError('Bounded completion grace required')
        self.stop();deadline=monotonic()+grace
        with self._lock:threads=tuple(self._active.values())
        for thread in threads:thread.join(max(0,deadline-monotonic()))
        return self.snapshot()

    def snapshot(self):
        with self._lock:return dict(submitted=self._submitted,in_flight=len(self._active),
            pending=len(self._pending),stopped=self._stopped,scheduling=dict(self._scheduling))


def run_background(capture_path,baseline,output,client,*,clock=lambda:datetime.now(UTC),
                   timer=monotonic,stop=None,wait=None,completion_grace=31):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    stop=stop or Event();wait=wait or stop.wait
    pool=BackgroundAdvisers(client,baseline,clock=clock,timer=timer)
    started=timer();active=None;seen=set();previous=b'';counts=Counter();owners=Counter()
    notes={};reason='STOPPED';before=client.requests;initial_usage=client.usage()
    def persist(log):
        for record in pool.drain():
            counts[record.status]+=1
            item=asdict(record)
            log.write(json.dumps(item,sort_keys=True)+'\n');log.flush()
            if record.view in {'WATCH','PASS'}:
                key=record.engine+':'+record.symbol
                old=notes.get(key)
                if old is None or item['evidence_captured_at']>old['evidence_captured_at']:notes[key]=item
        # This file is a private research view, never an execution input.
        state=dict(scope='PRIVATE_BACKGROUND_ADVISORY_STATE_V1',execution_authorized=False,
                   desktop_decision_input=False,records=list(notes.values()))
        target=output/'private-advisory-state.json'
        temporary=output/'private-advisory-state.tmp'
        temporary.write_text(json.dumps(state,indent=2),encoding='utf-8');temporary.replace(target)
    with (output/'private-observations.jsonl').open('x',encoding='utf-8') as log:
        try:
            while not stop.is_set():
                persist(log);now=timer()
                if active is None and now-started>=900:reason='STARTUP_TIMEOUT';break
                if active is not None and now-active>=300:reason='TIME_LIMIT';break
                if client.requests-before>=20:reason='REQUEST_LIMIT';break
                if len(seen)>=100:reason='RECORD_LIMIT';break
                path=Path(capture_path)
                if not path.exists():wait(.25);continue
                with path.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:reason='INPUT_BOUND_EXCEEDED';break
                end=raw.rfind(b'\n')
                if end<0:
                    if previous:reason='CAPTURE_REVISED';break
                    wait(.25);continue
                complete=raw[:end+1]
                if not complete.startswith(previous):reason='CAPTURE_REVISED';break
                previous=complete
                try:records=decode_capture(complete,baseline)
                except Exception:reason='INVALID_CAPTURE';break
                if any(s.source!='WEBULL_OPENAPI' for s,c in records):reason='NON_NATIVE_CAPTURE';break
                for snapshot,context in records:
                    identity=(snapshot.engine,snapshot.event_id)
                    if identity in seen:continue
                    seen.add(identity);owners[snapshot.engine.value]+=1
                    if active is None and snapshot.fresh(clock()):active=timer()
                    pool.offer(snapshot,context)
                if stop.is_set():break
                pool.pump();wait(.25)
        except KeyboardInterrupt:reason='INTERRUPTED'
        except Exception:reason='LOCAL_OBSERVER_FAILED'
        finally:
            active_end=timer()
            state=pool.finish(completion_grace);persist(log)
    usage=client.usage()
    report=dict(scope='BACKGROUND_ONLINE_GROK_OBSERVER_V2',stop_reason=reason,orders=0,
        execution_authorized=False,baseline_modified=False,desktop_order_review_enabled=False,
        desktop_decision_input=False,online_requests_enabled=True,baseline_sha256=baseline,
        inference_attempts=client.requests-before,records_by_engine=dict(owners),statuses=dict(counts),
        scheduling=state,advisory_records_retained=len(notes),accounting_final=state['in_flight']==0,
        usage={**{k:v-initial_usage.get(k,0) for k,v in usage.items()},
               'attempts_without_reported_usage':client.requests-before-(usage['responses_with_usage']-initial_usage['responses_with_usage'])},
        client_diagnostics=client.diagnostics(),elapsed_seconds=round(timer()-started,3),
        startup_wait_seconds=round((active_end if active is None else active)-started,3),
        active_observation_seconds=0 if active is None else round(active_end-active,3),
        limitations=['Background research observation only; no decision or order dispatch',
                     'Expired quote advice retains its reason privately but remains ADVICE_EXPIRED',
                     'In-flight requests at shutdown may incur charges not yet reported',
                     'Native execution requires its own qualified candidate and fresh quote/risk checks'])
    (output/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
