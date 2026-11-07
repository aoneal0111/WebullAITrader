"""Bounded actual market-data handoff to a worker and disabled temporary simulator."""
from datetime import UTC, datetime
from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from app.asset_modules.admission_controller import PaperAdmissionController
from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.crypto_signals import CryptoQuote, CryptoSignalSettings, propose
from app.asset_modules.crypto_worker import CryptoSignalWorker, CryptoWorkerController
from app.asset_modules.engine_worker import WorkerState
from .bar_alignment import check as check_alignment
from .reliability import category


class DisabledHandoff:
    """No entry-enable method, external clients, or installed database paths."""
    def __init__(self, directory, clock):
        self.clock = clock
        self.authorization_attempts = 0
        self.assessments = 0
        self.worker = None
        self.parent = None
        self.ledger = CryptoPaper(Path(directory)/'crypto.sqlite3')
        try:
            self.shared = PaperAdmissionController(Path(directory)/'shared.sqlite3')
            self.shared.configure_account('isolated-data-check', capital=D(1000), loss_budget=D(50))
            self.router = CryptoAllocationRouter(self.shared, self.ledger,
                account_id='isolated-data-check', authorize=self._deny)
            self.worker = CryptoSignalWorker(timeout_seconds=10)
            self.parent = CryptoWorkerController(self.worker, self.router, settings=CryptoSignalSettings())
            self.worker.start()
            deadline = monotonic()+10
            while monotonic()<deadline:
                reply = self.worker.poll()
                if reply and reply['kind']=='READY':
                    return
                if self.worker.state == WorkerState.FAILED:
                    break
                sleep(.01)
            raise RuntimeError('Worker startup failed')
        except BaseException:
            self.close()
            raise

    def _deny(self, *args):
        self.authorization_attempts += 1
        return False

    def state(self):
        state = self.ledger.snapshot()
        return dict(entries_enabled=self.router.enabled,
                    positions=len(state['positions']), simulated_fills=len(state['events']),
                    active_allocations=self.shared.snapshot('isolated-data-check')['active_commands'],
                    authorization_attempts=self.authorization_attempts)

    def assess(self, history, quote, settings):
        if settings != self.parent.settings or self.router.enabled:
            raise ValueError('Frozen disabled handoff required')
        submitted = self.clock()
        expected, reason = propose(history,
            CryptoQuote(history.symbol, quote.bid, quote.ask, quote.timestamp), submitted, settings)
        quotes = {history.symbol: quote}
        started = monotonic()
        result = self.parent.submit(history, quotes, now=submitted)
        if result['entry'] != 'PENDING_WORKER':
            raise RuntimeError('Worker submission failed')
        self.assessments += 1
        deadline = started+10
        while monotonic()<deadline:
            completed = self.clock()
            result = self.parent.poll(quotes, now=completed)
            if result['entry'] != 'PENDING_WORKER':
                break
            sleep(.01)
        else:
            raise RuntimeError('Worker completion failed')
        completed = self.clock()
        age = (completed-quote.timestamp).total_seconds()
        elapsed = (completed-submitted).total_seconds()
        state = self.state()
        wanted = 'ENTRIES_DISABLED' if expected is not None else reason
        ready = (result['entry'] == wanted and 0<=age<=2 and 0<=elapsed<=2
                 and self.worker.state == WorkerState.READY
                 and not any(state.values()))
        return dict(ready=ready, controller_result=result['entry'],
                    worker_response_verified=self.worker.state==WorkerState.READY,
                    submitted_at=submitted.isoformat(), completed_at=completed.isoformat(),
                    completion_seconds=elapsed, quote_age_at_completion_seconds=age,
                    **state)

    def close(self):
        if self.parent is not None:
            self.parent.close()
        elif self.worker is not None:
            self.worker.close()
        self.ledger.close()


def check(provider, *, clock=lambda:datetime.now(UTC), timer=monotonic, sleeper=sleep, progress=print):
    report = dict(ready=False, status='WORKER_NOT_STARTED', cycles=[])
    final_state = None
    assessments = 0
    try:
        with TemporaryDirectory(prefix='atlas-provider-worker-') as directory:
            handoff = DisabledHandoff(directory, clock)
            try:
                report = check_alignment(provider, clock=clock, timer=timer, sleeper=sleeper,
                                         progress=progress, handoff=handoff.assess)
                final_state = handoff.state()
                assessments = handoff.assessments
            finally:
                handoff.close()
    except Exception as exc:
        report.update(ready=False, status=category(exc))
    report['scope'] = 'READ_ONLY_CRYPTO_PROVIDER_WORKER_V1'
    root = Path(__file__).resolve().parents[1]
    hashes = report.setdefault('source_hashes', {})
    for name in ('crypto_research/provider_worker.py', 'asset_modules/crypto_worker.py',
                 'asset_modules/engine_worker.py', 'asset_modules/crypto_paper.py',
                 'asset_modules/crypto_allocation.py', 'asset_modules/admission_controller.py'):
        hashes[name] = sha256((root/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    report.update(orders=0, broker_orders=0, grok_requests=0, strategy_changes=0,
                  installed_databases_opened=0, baseline_modified=False,
                  worker_assessments=assessments, temporary_ledger_state=final_state)
    report['ready'] = (report['ready'] and assessments==6 and final_state is not None
                       and not any(final_state.values()))
    if report['ready']:
        report['status'] = 'BOUNDED_PROVIDER_WORKER_HANDOFF_PASSED'
    elif report['status']=='BOUNDED_BAR_QUOTE_ALIGNMENT_PASSED':
        report['status'] = 'PROVIDER_WORKER_HANDOFF_FAILED'
    report['limitations'] = [
        'Two actual provider pairs and three cycles; not strategy performance or desktop validation',
        'Entries remain disabled throughout; any valid entry must return ENTRIES_DISABLED',
        'Temporary ledgers must remain empty; no synthetic prices or forced signals are injected',
        'Current quote is rechecked at completion; no fresh quote is fetched after worker assessment',
        'Compiled default signal settings; no installed strategy settings, Grok or trading activation']
    return report
