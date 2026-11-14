"""Opt-in native desktop crypto observation; no new entry or model authority."""
from datetime import UTC,datetime
from threading import get_ident

from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.crypto_history_adapter import snapshot
from app.asset_modules.crypto_signals import POLICY,CryptoSignalSettings
from app.asset_modules.crypto_worker import CryptoSignalWorker,CryptoWorkerController
from app.asset_modules.engine_worker import WorkerState
from app.asset_modules.supervisor import CryptoSupervisor
from app.crypto_research.runtime import CryptoResearchRuntime


class NativeCryptoObserver(CryptoSupervisor):
    native_observation_only=True
    symbols=('BTC/USD','ETH/USD')

    def __init__(self,paper,runtime,*,entry_router,clock=None,evidence_sink=None):
        if not isinstance(runtime,CryptoResearchRuntime) or not isinstance(entry_router,CryptoAllocationRouter):
            raise ValueError('Native crypto observation requires research runtime and shared PAPER allocation')
        super().__init__(paper,runtime.latest,None,entry_router=entry_router)
        self.runtime=runtime
        self._clock=clock or (lambda:datetime.now(UTC))
        self._native_controller=None
        self._cursor=0
        self._seen={}
        self._evidence_sink=evidence_sink
        self._evidence_outcomes={}
        self._native_metrics=dict(assessments_submitted=0,assessments_completed=0,
            qualifying_signals_blocked=0,input_waits=0,worker_failures=0,
            worker_owner_thread=None,last_outcomes={})
        self.status='Crypto strategy observation ready · entries disabled'

    @CryptoSupervisor.entries_enabled.setter
    def entries_enabled(self,value):
        if type(value) is not bool or value:
            raise ValueError('Native crypto observer cannot enable entries')
        CryptoSupervisor.entries_enabled.fset(self,False)

    def metrics(self):
        with self.gate:
            return {**self._native_metrics,'last_outcomes':dict(self._native_metrics['last_outcomes']),
                'entries_enabled':False,'grok_requests':0,'entry_authority':False,
                'worker_state':None if self._native_controller is None else self._native_controller.worker.state.value}

    def _outcome(self,symbol,reason):
        # All reasons come from native enums/validated worker output, never a provider error body.
        self._native_metrics['last_outcomes'][symbol]=reason
        self.status=f'Crypto strategy observation · entries disabled · {symbol}: {reason}'
        if self._evidence_outcomes.get(symbol)!=reason:
            self._evidence_outcomes[symbol]=reason
            self._record('CRYPTO_NATIVE_OBSERVER_OUTCOME',dict(symbol=symbol,reason=reason,
                at=self._clock(),policy=POLICY,entries_enabled=False,execution_authorized=False))

    def _record(self,kind,payload):
        if self._evidence_sink is not None:
            try:self._evidence_sink(kind,payload)
            except Exception:pass  # Observation cannot stop independent protection.

    def tick(self):
        # Preserve worker thread ownership: the GUI never polls this handle.
        raise RuntimeError('Native crypto observer is polled by its own owner thread')

    def _step(self,controller):
        try:
            quotes={row.pair.canonical_symbol:row for row in self.source()}
        except Exception:
            quotes={}  # Still poll the worker deadline and maintain independent protection.
        now=self._clock()
        with self.gate:
            self.entry_router.set_enabled(False)
            pending=controller.pending
            result=controller.poll(quotes,now=now)
            if pending is not None and controller.pending is None:
                symbol=pending[0].symbol
                self._native_metrics['assessments_completed']+=1
                if result['entry']=='ENTRIES_DISABLED':
                    self._native_metrics['qualifying_signals_blocked']+=1
                self._outcome(symbol,result['entry'])
                quote=quotes.get(symbol)
                self._record('CRYPTO_NATIVE_OBSERVER_RESULT',dict(symbol=symbol,reason=result['entry'],
                    at=now,quote=quote,policy=POLICY,worker_state=controller.worker.state.value,
                    entries_enabled=False,execution_authorized=False))
                self.paper.record_decision({'symbol':symbol,'action':'OBSERVE','reason':POLICY},result['entry'])
            if controller.worker.state in {WorkerState.FAILED,WorkerState.CLOSED}:
                self._native_metrics['worker_failures']+=1
                self.status='Crypto strategy worker unavailable · entries disabled; protection remains separate'
                return False
            if controller.worker.state is not WorkerState.READY or controller.pending is not None:
                return True
            for _ in self.symbols:
                symbol=self.symbols[self._cursor%len(self.symbols)];self._cursor+=1
                frame=snapshot(self.runtime,symbol,now=now)
                if frame.status!='READY':
                    self._native_metrics['input_waits']+=1
                    self._outcome(symbol,frame.status)
                    continue
                # One assessment per completed bar per symbol. Observation is not a
                # measurement of tick-by-tick entry responsiveness or performance.
                last=frame.history.bars[-1]
                identity=(last.end,last.high,last.low,last.close)
                if self._seen.get(symbol)==identity:
                    continue
                supplied={**quotes,symbol:frame.quote}
                result=controller.submit(frame.history,supplied,now=now)
                if result['entry']=='PENDING_WORKER':
                    self._seen[symbol]=identity
                    self._native_metrics['assessments_submitted']+=1
                    self._record('CRYPTO_NATIVE_OBSERVER_INPUT',dict(symbol=symbol,at=now,
                        history=frame.history,quote=frame.quote,policy=POLICY,
                        entries_enabled=False,execution_authorized=False))
                self._outcome(symbol,result['entry'])
                break
        return True

    def _protect_run(self):
        while not self.stop_event.is_set():
            try:
                quotes={row.pair.canonical_symbol:row for row in self.source()}
                self._protect(quotes,self._clock())
            except Exception as error:
                self.protection_status=f'ERROR: {type(error).__name__}'
            self.stop_event.wait(1)

    def _run(self):
        controller=None
        try:
            self._seen.clear()
            # Construct, poll and close the process handle on exactly this thread.
            worker=CryptoSignalWorker(timeout_seconds=2)
            controller=CryptoWorkerController(worker,self.entry_router,settings=CryptoSignalSettings())
            with self.gate:
                self._native_controller=controller
                self._native_metrics['worker_owner_thread']=get_ident()
            worker.start()
            while not self.stop_event.is_set():
                try:
                    if not self._step(controller):
                        break  # No automatic restart after worker failure.
                except Exception as error:
                    self.status=f'Crypto strategy observation unavailable: {type(error).__name__}'
                    # Stop signal work on unexpected failure; protection thread is independent.
                    with self.gate:self._native_metrics['worker_failures']+=1
                    break
                self.stop_event.wait(.05 if controller.pending is not None or worker.state is WorkerState.STARTING else 1)
        except Exception as error:
            self.status=f'Crypto strategy worker unavailable: {type(error).__name__}'
            with self.gate:self._native_metrics['worker_failures']+=1
        finally:
            if controller is not None:
                controller.close()
            self.entry_router.set_enabled(False)
