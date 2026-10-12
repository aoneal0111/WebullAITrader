"""Synthetic subprocess-to-parent ledger check; no Atlas GUI or network."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from app.asset_modules.contract_logic import Bar, Contract, Quote
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.engine_worker import WorkerState
from app.asset_modules.futures_paper import FuturesPaper
from app.asset_modules.futures_signals import FuturesHistory, FuturesPaperController, FuturesSignalSettings
from app.asset_modules.futures_worker import FuturesSignalWorker, FuturesWorkerController


def _ready(worker):
    until = time.monotonic()+10
    while time.monotonic() < until:
        if worker.poll(): return
        if worker.state == WorkerState.FAILED: raise RuntimeError(worker.failure_reason)
        time.sleep(.01)
    raise RuntimeError('Worker startup did not complete')


def _complete(bridge, quotes, now):
    until = time.monotonic()+10
    while time.monotonic() < until:
        result = bridge.poll(quotes,now=now)
        if result['entry'] != 'PENDING_WORKER': return result
        time.sleep(.01)
    raise RuntimeError('Worker handoff did not complete')


def main():
    now = datetime(2026,10,12,15,tzinfo=UTC)
    c = Contract('SYNTHETIC_MICRO',EngineId.FUTURES,now+timedelta(days=30),D(5),D('.25'),D(1000))
    bars = tuple(Bar(now-timedelta(minutes=21-i),D(100),D('99.75'),D(100)) for i in range(21))
    h = FuturesHistory(c.symbol,bars+(Bar(now,D('100.25'),D(100),D('100.25')),))
    q = Quote(c.symbol,D('100.25'),D('100.50'),now,now)
    with TemporaryDirectory(prefix='atlas-futures-worker-') as root:
        ledger = FuturesPaper(Path(root)/'synthetic.sqlite3')
        worker = FuturesSignalWorker(timeout_seconds=10)
        controller = FuturesPaperController(ledger,settings=FuturesSignalSettings(),authorize=lambda *args:True)
        bridge = FuturesWorkerController(worker,controller)
        try:
            worker.start(); _ready(worker)
            bridge.submit(c,h,{c.symbol:q},now=now)
            disabled = _complete(bridge,{c.symbol:q},now)
            ledger.set_enabled(True)
            bridge.submit(c,h,{c.symbol:q},now=now)
            opened = _complete(bridge,{c.symbol:q},now)
            if opened['entry'] != 'OPENED': raise RuntimeError(opened['entry'])
            p = opened['proposal'].plan
            bridge.close()
            later = now+timedelta(seconds=1)
            exit_q = Quote(c.symbol,p.target,p.target+c.tick,later,later)
            protected = bridge.poll({c.symbol:exit_q},now=later)
            remaining = len(ledger.snapshot()['positions'])
            passed = (disabled['entry']=='ENTRIES_DISABLED' and worker.pid != os.getpid()
                      and protected['protection'].get(opened['proposal'].intent_id)=='TARGET'
                      and not remaining and not ledger.enabled)
            print(json.dumps({'scope':'SYNTHETIC_FUTURES_WORKER_HANDOFF_NOT_STRATEGY_PERFORMANCE',
                              'passed':passed,'separate_process':worker.pid != os.getpid(),
                              'default_entry':disabled['entry'],'enabled_entry':opened['entry'],
                              'protection_after_worker_closed':list(protected['protection'].values()),
                              'positions_remaining':remaining,'network_requests':0},indent=2))
            return 0 if passed else 1
        finally:
            bridge.close(); ledger.close()


if __name__ == '__main__':
    raise SystemExit(main())
