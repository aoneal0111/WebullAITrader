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
    def record_exit_decision(self,payload):
        if self._journal is not None:
            return self._journal.submit('EXIT_DECISION',dict(payload))
        return False

    def __init__(self,capture,baseline,*,journal=None):
        if not isinstance(capture,ContextCapture) or baseline!=capture._baseline:
            raise ValueError('Matching private capture and frozen baseline required')
        self._capture,self._baseline=capture,baseline
        self._journal=journal
        self._queue=Queue(maxsize=16);self._stop=Event();self._lock=Lock()
        self._closed=False;self._counts=Counter(queued=0,bars_queued=0,assessments_queued=0);self._bars=OrderedDict()
        self._timing={};self._preview_counts=Counter(samples=0);self._preview_ages={}
        self._confirmation_counts=Counter(samples=0);self._confirmation_ages={}
        self._eligibility_cache=OrderedDict()
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

    def record_market_eligibility(self,symbol,*,tradable,halted,at):
        """One-way market-state evidence before setup/entry filters, at most 1Hz.

        State changes bypass coalescing. No eligibility, risk, source timestamp
        or provider request is changed by recording this heartbeat.
        """
        try:
            if getattr(self,'_journal',None) is None:return False
            if type(tradable) is not bool or type(halted) is not bool or at.utcoffset() is None:return False
            symbol=symbol.strip().upper()
            if not symbol or len(symbol)>32:return False
            with self._lock:
                if self._closed:return False
                previous=self._eligibility_cache.get(symbol)
                if (previous is not None and previous[0:2]==(tradable,halted)
                        and timedelta(0)<=at-previous[2]<timedelta(seconds=1)):
                    self._counts['eligibility_unchanged_coalesced']+=1;return False
                accepted=self._journal.submit('MARKET_ELIGIBILITY',dict(symbol=symbol,
                    tradable=tradable,halted=halted,known_at=at,source='NATIVE_SCANNER_OBSERVATION'))
                if accepted:
                    self._eligibility_cache.pop(symbol,None)
                    self._eligibility_cache[symbol]=(tradable,halted,at)
                    if len(self._eligibility_cache)>1024:self._eligibility_cache.popitem(last=False)
                    self._counts['eligibility_submitted']+=1
                return accepted
        except Exception:return False

    def remember_bars(self,symbol,bars,*,known_at):
        # Copy a bounded tuple; no history sorting/serialization or disk I/O here.
        try:
            if self._journal is not None:
                self._journal.submit('BARS',dict(symbol=symbol,bars=tuple(bars[-30:]),known_at=known_at))
            return self._put(('BARS',symbol,tuple(bars[-30:]),known_at))
        except Exception:return False

    def record(self,*,engine,quote,source,at,baseline_result,setup_state,preview=None):
        event_id=uuid4().hex
        if self._journal is not None:
            self._journal.submit('ASSESSMENT',dict(event_id=event_id,engine=engine,quote=quote,
                assessed_at=at,baseline_result=baseline_result,setup_state=setup_state,preview=preview))
        return self._put(('ASSESS',engine,quote,source,at,baseline_result,setup_state,self._capture._clock(),preview,event_id))

    def record_scalper_preview(self,snapshot,assessment):
        """Numeric initial-stream rejection diagnostics; no requests or authority."""
        try:
            if self._journal is not None:
                self._journal.submit('STREAM_ASSESSMENT',dict(engine='QUICK_SCALPER',snapshot=snapshot,
                    decision=assessment.decision,reason=assessment.reason))
            from app.strategies.warrior_momentum.quick_scalper import QuickScalpSnapshot,QuickScalpAssessment
            if (not isinstance(snapshot,QuickScalpSnapshot) or not isinstance(assessment,QuickScalpAssessment)
                    or snapshot.setup_type!='QUICK_SCALP_STREAM' or assessment.reason!='PROVIDER_DATA_STALE'):
                return
            unit=timedelta(microseconds=1)
            ages={name:int((snapshot.decision_at-getattr(snapshot,name+'_timestamp'))//unit)
                  for name in ('last','bid','ask')}
            # Match the current policy's order; old LAST alone is permitted.
            first=('LAST_FUTURE' if ages['last']<0 else
                   'BID_FUTURE' if ages['bid']<0 else 'BID_STALE' if ages['bid']>5000000 else
                   'ASK_FUTURE' if ages['ask']<0 else 'ASK_STALE' if ages['ask']>5000000 else 'UNCLASSIFIED')
            with self._lock:
                if self._closed:return
                self._preview_counts['samples']+=1
                self._preview_counts['first_blocker_'+first]+=1
                if any(age<0 for age in ages.values()):self._preview_counts['future_component_prevents_refresh']+=1
                for name,age in ages.items():
                    for flag,test in (('future',age<0),('older_than_five_seconds',age>5000000)):
                        if test:self._preview_counts[name+'_'+flag]+=1
                    old=self._preview_ages.setdefault(name,dict(min=age,max=age))
                    old['min']=min(old['min'],age);old['max']=max(old['max'],age)
        except Exception:
            # Diagnostics must not change the policy result or cause an order.
            try:self._count('preview_diagnostics_failed')
            except Exception:pass

    def _compare_scalper_confirmation(self,preview,quote,at):
        """Compare the exact authorization input with its returned REST quote."""
        from app.strategies.warrior_momentum.quick_scalper import QuickScalpSnapshot
        if not isinstance(preview,QuickScalpSnapshot) or preview.symbol!=quote.symbol:
            self._count('confirmation_preview_missing_or_mismatched');return
        unit=timedelta(microseconds=1)
        stream_ages={name:int((preview.decision_at-getattr(preview,name+'_timestamp'))//unit)
                     for name in ('last','bid','ask')}
        rest_ages={name:int((at-getattr(quote,name+'_timestamp'))//unit)
                   for name in ('last','bid','ask')}
        def state(ages):
            if any(age<0 for age in ages.values()):return 'FUTURE'
            return 'STALE' if any(ages[n]>5000000 for n in ('bid','ask')) else 'FRESH'
        deltas={name:int((getattr(quote,name+'_timestamp')-getattr(preview,name+'_timestamp'))//unit)
                for name in ('bid','ask')}
        values={**{'stream_'+n+'_age':v for n,v in stream_ages.items()},
                **{'rest_'+n+'_age':v for n,v in rest_ages.items()},
                **{n+'_timestamp_advance':v for n,v in deltas.items()},
                'preview_to_confirmation':int((at-preview.decision_at)//unit)}
        with self._lock:
            self._confirmation_counts['samples']+=1
            self._confirmation_counts['STREAM_'+state(stream_ages)+'_REST_'+state(rest_ages)]+=1
            for name,delta in deltas.items():
                self._confirmation_counts[name+'_timestamp_'+('NEWER' if delta>0 else 'OLDER' if delta<0 else 'SAME')]+=1
            if quote.bid==preview.bid and quote.ask==preview.ask:
                self._confirmation_counts['same_bid_and_ask_prices']+=1
            if all(v==0 for v in deltas.values()) and (quote.bid!=preview.bid or quote.ask!=preview.ask):
                self._confirmation_counts['changed_prices_with_same_quote_timestamps']+=1
            for name,value in values.items():
                old=self._confirmation_ages.setdefault(name,dict(min=value,max=value))
                old['min']=min(old['min'],value);old['max']=max(old['max'],value)

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
                _,engine,quote,source,native_at,result,setup,enqueued_at,preview,event_id=item
                from app.strategies.warrior_momentum.execution_quote import ExecutionQuoteSnapshot,WebullExecutionQuoteSource
                if (not isinstance(source,WebullExecutionQuoteSource) or not isinstance(quote,ExecutionQuoteSnapshot)
                        or quote.bid_timestamp!=quote.ask_timestamp):
                    self._count('unsupported_quote_provenance');continue
                captured_now=self._capture._clock()
                # The native market event can precede the synchronous REST
                # confirmation. Keep both clocks; never rewrite provider time.
                at=quote.confirmed_at if quote.confirmed_at is not None else native_at
                if engine is EngineId.SCALPER and preview is not None:
                    self._compare_scalper_confirmation(preview,quote,native_at)
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
                snapshot=Snapshot(engine,event_id,quote.symbol,quote.bid,quote.ask,
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
        with self._lock:
            counts=dict(self._counts);timing={k:dict(v) for k,v in self._timing.items()}
            preview=dict(scope='INITIAL_STREAM_POLICY_FRESHNESS_REJECTIONS',
                counts=dict(self._preview_counts),age_microseconds={k:dict(v) for k,v in self._preview_ages.items()},
                provider_timestamps_modified=False,policy_results_modified=False)
            confirmation=dict(scope='PAIRED_SCALPER_STREAM_AND_REST_CONFIRMATION_V1',
                counts=dict(self._confirmation_counts),
                microseconds={k:dict(v) for k,v in self._confirmation_ages.items()},
                provider_timestamps_modified=False,policy_results_modified=False,
                extra_provider_requests=0,
                limitations=['Five-second book-age classification only; not full policy approval',
                             'Sequential native preview and REST confirmation; not simultaneous quotes'])
        return dict(scope='PASSIVE_NATIVE_ENGINE_CONTEXT_HOOKS',tap_stopped=not self._thread.is_alive(),
                    tap_counts=counts,timing=timing,scalper_preview_freshness=preview,scalper_confirmation_comparison=confirmation,capture=report,online_observer_ready=False)
