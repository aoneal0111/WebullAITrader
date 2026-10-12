"""Fabricated shared allocation and options subprocess check; no network."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.asset_modules.admission_controller import EntryReservation, PaperAdmissionController
from app.asset_modules.contract_logic import Bar, Contract, Quote
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.options_allocation import OptionsAllocationRouter
from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.options_signals import OptionsHistory, OptionsPaperController, OptionsSignalSettings
from app.asset_modules.options_worker import OptionsSignalWorker, OptionsWorkerController
from app.asset_modules.options_worker_check import _ready, _complete


def main():
    now = datetime(2026,10,12,15,tzinfo=UTC)
    c = Contract('SYNTHETIC_SPY_CALL',EngineId.OPTIONS,now+timedelta(days=30),D(100),D('.01'),D(1),'SPY','CALL',D(100))
    bars = tuple(Bar(now-timedelta(minutes=21-i),D(100),D('99.75'),D(100)) for i in range(21))
    h = OptionsHistory('SPY',bars+(Bar(now,D('100.25'),D(100),D('100.25')),))
    uq = Quote('SPY',D('100.25'),D('100.26'),now,now)
    q = Quote(c.symbol,D('1.99'),D(2),now,now)
    with TemporaryDirectory(prefix='atlas-options-allocation-') as root:
        shared = PaperAdmissionController(Path(root)/'shared.sqlite3')
        shared.configure_account('synthetic-pool',capital=D(3000),loss_budget=D(50))
        equity = EntryReservation('synthetic-equity','synthetic-pool',EngineId.WARRIOR,
            'WARRIOR_MOMENTUM_V1|SPY|synthetic-equity','SPY','synthetic-v1','BUY',D(22),D(100),
            D('99.90'),D(1),D(2200),D(10),now,now+timedelta(seconds=30))
        reserved = shared.reserve(equity,now=now)
        ledger = OptionsPaper(Path(root)/'options.sqlite3')
        router = OptionsAllocationRouter(shared,ledger,account_id='synthetic-pool')
        parent = OptionsPaperController(ledger,settings=OptionsSignalSettings(),authorize=lambda *a:True,entry_router=router)
        worker = OptionsSignalWorker(timeout_seconds=10)
        bridge = OptionsWorkerController(worker,parent)
        try:
            worker.start(); _ready(worker)
            ledger.set_enabled(True)
            bridge.submit(h,uq,(c,),{c.symbol:q},now=now)
            blocked = _complete(bridge,uq,{c.symbol:q},now)
            shared.abandon_unsubmitted(equity.command_id,equity.engine)
            bridge.submit(h,uq,(c,),{c.symbol:q},now=now)
            opened = _complete(bridge,uq,{c.symbol:q},now)
            if opened['entry'] != 'OPENED': raise RuntimeError(opened['entry'])
            held = shared.snapshot('synthetic-pool')['reserved_capital']
            p = opened['proposal'].plan
            bridge.close()
            later = now+timedelta(seconds=1)
            closing = Quote(c.symbol,p.target,p.target+c.tick,later,later)
            protected = bridge.poll(None,{c.symbol:closing},now=later)
            final = shared.snapshot('synthetic-pool')
            balance = shared.allocation_balance('synthetic-pool')
            passed = (reserved.state=='RESERVED' and blocked['entry']=='SHARED_CAPITAL_LIMIT'
                      and held==D('813.2') and not final['active_commands']
                      and protected['protection'].get(opened['proposal'].intent_id)=='TARGET'
                      and not ledger.snapshot()['positions'] and balance['capital_ceiling']==D(3000)
                      and balance['options_realized_pnl']==D('26.8'))
            print(json.dumps({'scope':'SYNTHETIC_OPTIONS_SHARED_ALLOCATION_NOT_STRATEGY_PERFORMANCE',
                'passed':passed,'with_equity_reserved':blocked['entry'],
                'after_equity_reservation_released':opened['entry'],'options_reserved_capital':str(held),
                'protection_after_worker_closed':list(protected['protection'].values()),
                'shared_active_commands':final['active_commands'],'shared_reserved_capital':str(final['reserved_capital']),
                'capital_ceiling':str(balance['capital_ceiling']),'synthetic_options_realized_pnl':str(balance['options_realized_pnl']),
                'network_requests':0},indent=2))
            return 0 if passed else 1
        finally:
            bridge.close(); ledger.close()


if __name__ == '__main__':
    raise SystemExit(main())
