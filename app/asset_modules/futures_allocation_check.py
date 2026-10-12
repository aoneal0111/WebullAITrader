"""Synthetic shared allocation and subprocess check; no broker, AI or GUI."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.asset_modules.admission_controller import EntryReservation, PaperAdmissionController
from app.asset_modules.contract_logic import Bar, Contract, Quote
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.futures_allocation import FuturesAllocationRouter
from app.asset_modules.futures_paper import FuturesPaper
from app.asset_modules.futures_signals import FuturesHistory, FuturesPaperController, FuturesSignalSettings
from app.asset_modules.futures_worker import FuturesSignalWorker, FuturesWorkerController
from app.asset_modules.futures_worker_check import _ready, _complete


def main():
    now = datetime(2026,10,12,15,tzinfo=UTC)
    c = Contract('SYNTHETIC_MICRO',EngineId.FUTURES,now+timedelta(days=30),D(5),D('.25'),D(1000))
    bars = tuple(Bar(now-timedelta(minutes=21-i),D(5000),D('4999.75'),D(5000)) for i in range(21))
    h = FuturesHistory(c.symbol,bars+(Bar(now,D('5000.25'),D(5000),D('5000.25')),))
    q = Quote(c.symbol,D('5000.25'),D('5000.50'),now,now)
    with TemporaryDirectory(prefix='atlas-futures-allocation-') as root:
        shared = PaperAdmissionController(Path(root)/'shared.sqlite3')
        shared.configure_account('synthetic-pool',capital=D(3000),loss_budget=D(50))
        equity = EntryReservation('synthetic-equity','synthetic-pool',EngineId.WARRIOR,
            'WARRIOR_MOMENTUM_V1|SPY|synthetic-equity','SPY','synthetic-v1','BUY',D(10),D(100),
            D('99.90'),D(1),D(1000),D(10),now,now+timedelta(seconds=30))
        reserved = shared.reserve(equity,now=now)
        ledger = FuturesPaper(Path(root)/'futures.sqlite3')
        router = FuturesAllocationRouter(shared,ledger,account_id='synthetic-pool')
        parent = FuturesPaperController(ledger,settings=FuturesSignalSettings(),
                                       authorize=lambda *args:True,entry_router=router)
        worker = FuturesSignalWorker(timeout_seconds=10)
        bridge = FuturesWorkerController(worker,parent)
        try:
            worker.start(); _ready(worker)
            ledger.set_enabled(True)
            bridge.submit(c,h,{c.symbol:q},now=now)
            blocked = _complete(bridge,{c.symbol:q},now)
            shared.abandon_unsubmitted(equity.command_id,equity.engine)
            bridge.submit(c,h,{c.symbol:q},now=now)
            opened = _complete(bridge,{c.symbol:q},now)
            if opened['entry']!='OPENED': raise RuntimeError(opened['entry'])
            held = shared.snapshot('synthetic-pool')['reserved_capital']
            p = opened['proposal'].plan
            bridge.close()
            later = now+timedelta(seconds=1)
            closing = Quote(c.symbol,p.target,p.target+c.tick,later,later)
            protected = bridge.poll({c.symbol:closing},now=later)
            final = shared.snapshot('synthetic-pool')
            balance = shared.allocation_balance('synthetic-pool')
            passed = (reserved.state=='RESERVED' and blocked['entry']=='SHARED_CAPITAL_LIMIT'
                      and held==D(2009) and not final['active_commands']
                      and protected['protection'].get(opened['proposal'].intent_id)=='TARGET'
                      and not ledger.snapshot()['positions'] and balance['capital_ceiling']==D(3000))
            print(json.dumps({'scope':'SYNTHETIC_SHARED_ALLOCATION_NOT_STRATEGY_PERFORMANCE',
                              'passed':passed,'with_equity_reserved':blocked['entry'],
                              'after_equity_reservation_released':opened['entry'],
                              'futures_reserved_capital':str(held),
                              'protection_after_worker_closed':list(protected['protection'].values()),
                              'shared_active_commands':final['active_commands'],
                              'shared_reserved_capital':str(final['reserved_capital']),
                              'capital_ceiling':str(balance['capital_ceiling']),'network_requests':0},indent=2))
            return 0 if passed else 1
        finally:
            bridge.close(); ledger.close()


if __name__=='__main__':
    raise SystemExit(main())
