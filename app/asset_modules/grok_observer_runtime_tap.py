"""Dormant native assessment hooks; market-thread work is a bounded queue put."""
from collections import Counter,OrderedDict
from datetime import timedelta
from queue import Queue,Empty,Full
from threading import Event,Lock,Thread
from uuid import uuid4

from .engine_catalog import EngineId
from .grok_observer import Snapshot
from .grok_observer_context import ContextBar,StrategyContext
from .grok_observer_capture import ContextCapture


class RuntimeContextTap:
    """Explicit injection only; shares market bars, never engine assessments.

    No model, execution, provider request or runtime-setting method exists here.
    The owner must close this outside engine/protection callbacks.
    """
    def __init__(self,capture,baseline):
        if not isinstance(capture,ContextCapture) or baseline!=capture._baseline:
            raise ValueError('Matching private capture and frozen baseline required')
        self._capture,self._baseline=capture,baseline
        self._queue=Queue(maxsize=16);self._stop=Event();self._lock=Lock()
        self._closed=False;self._counts=Counter(queued=0,bars_queued=0,assessments_queued=0);self._bars=OrderedDict()
        self._timing={}
        self._thread=Thread(target=self._run,name='atlas-observer-native-evidence',daemon=True)
        self._thread.start()

    def _put(self,item):
        try:
            with self._lock:
                if self._closed:self._counts['closed']+=1;return False
                try:self._queue.put_nowait(item)
                except Full:self._counts['queue_full']+=1;return False
                self._counts['queued']+=1
                if item[0]=='BARS':self._counts['bars_queued']+=1
                else:
                    self._counts['assessments_queued']+=1
                    if isinstance(item[1],EngineId):
                        self._counts['assessments_queued_'+item[1].value]+=1
                return True
        except Exception:return False

    def remember_bars(self,symbol,bars,*,known_at):
        # Copy a bounded tuple; no history sorting/serialization or disk I/O here.
        try:return self._put(('BARS',symbol,tuple(bars[-30:]),known_at))
        except Exception:return False

    def record(self,*,engine,quote,source,at,baseline_result,setup_state):
        return self._put(('ASSESS',engine,quote,source,at,baseline_result,setup_state,self._capture._clock()))

    def _count(self,key):
        with self._lock:self._counts[key]+=1

    def _measure(self,name,value):
        with self._lock:
            old=self._timing.setdefault(name,dict(min=value,max=value,samples=0))
            old['min']=min(old['min'],value);old['max']=max(old['max'],value);old['samples']+=1

    def _run(self):
        while not self._stop.is_set() or not self._queue.empty():
            try:item=self._queue.get(timeout=.05)
            except Empty:continue
            try:
                if item[0]=='BARS':
                    _,symbol,bars,known=item
                    self._bars.pop(symbol,None);self._bars[symbol]=(bars,known)
                    if len(self._bars)>128:self._bars.popitem(last=False)
                    continue
                _,engine,quote,source,native_at,result,setup,enqueued_at=item
                from app.strategies.warrior_momentum.execution_quote import ExecutionQuoteSnapshot,WebullExecutionQuoteSource
                if (not isinstance(source,WebullExecutionQuoteSource) or not isinstance(quote,ExecutionQuoteSnapshot)
                        or quote.bid_timestamp!=quote.ask_timestamp):
                    self._count('unsupported_quote_provenance');continue
                captured_now=self._capture._clock()
                # The native market event can precede the synchronous REST
                # confirmation. Keep both clocks; never rewrite provider time.
                at=quote.confirmed_at if quote.confirmed_at is not None else native_at
                native_age=(native_at-quote.bid_timestamp).total_seconds()
                assessed_age=(at-quote.bid_timestamp).total_seconds()
                self._measure('quote_age_at_native_event_seconds',native_age)
                self._measure('quote_age_at_confirmation_seconds',assessed_age)
                self._measure('native_event_to_confirmation_seconds',(at-native_at).total_seconds())
                if native_age<0:self._count('quote_after_native_event')
                current_age=(captured_now-quote.bid_timestamp).total_seconds()
                self._measure('quote_age_at_assessment_seconds',assessed_age)
                self._measure('quote_age_at_capture_seconds',current_age)
                self._measure('observer_queue_delay_seconds',(captured_now-enqueued_at).total_seconds())
                self._measure('confirmation_to_capture_seconds',(captured_now-at).total_seconds())
                if assessed_age<0:self._count('future_quote_at_assessment')
                if current_age<0:self._count('future_quote_at_capture')
                if current_age>2:self._count('stale_quote_at_capture')
                if quote.symbol not in self._bars:
                    self._count('missing_shared_market_history');continue
                bars,known=self._bars[quote.symbol]
                from app.strategies.warrior_momentum.models import MinuteBar
                if any(not isinstance(b,MinuteBar) or b.symbol!=quote.symbol for b in bars):
                    self._count('history_symbol_or_type_mismatch');continue
                if known>native_at:
                    self._count('history_not_available_at_assessment');continue
                completed=tuple(ContextBar(b.timestamp+timedelta(minutes=1),known,
                    b.open,b.high,b.low,b.close,b.volume) for b in bars if b.timestamp+timedelta(minutes=1)<=known)
                snapshot=Snapshot(engine,uuid4().hex,quote.symbol,quote.bid,quote.ask,
                    quote.bid_timestamp,quote.ask_timestamp,at,self._baseline,'WEBULL_OPENAPI')
                context=StrategyContext(engine,snapshot.event_id,quote.symbol,self._baseline,at,
                    'WEBULL_OPENAPI',setup,result,completed)
                accepted=self._capture.submit(snapshot,context,quote_basis='WEBULL_PROVIDER_QUOTE_TIME_SHARED')
                self._count('captured' if accepted else 'capture_refused')
            except Exception:self._count('invalid_native_evidence')

    def close(self):
        with self._lock:self._closed=True
        self._stop.set();self._thread.join(timeout=2)
        report=self._capture.close()
        with self._lock:counts=dict(self._counts);timing={k:dict(v) for k,v in self._timing.items()}
        return dict(scope='PASSIVE_NATIVE_ENGINE_CONTEXT_HOOKS',tap_stopped=not self._thread.is_alive(),
                    tap_counts=counts,timing=timing,capture=report,online_observer_ready=False)
