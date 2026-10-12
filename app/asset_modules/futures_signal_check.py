"""Offline signal-to-ledger smoke check. Fabricated data, not performance evidence."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.asset_modules.contract_logic import Bar, Contract, Quote
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.futures_paper import FuturesPaper
from app.asset_modules.futures_signals import FuturesHistory, FuturesPaperController, FuturesSignalSettings


def main():
    now = datetime(2026, 10, 12, 15, tzinfo=UTC)
    c = Contract('SYNTHETIC_MICRO', EngineId.FUTURES, now+timedelta(days=30), D(5), D('.25'), D(1000))
    bars = tuple(Bar(now-timedelta(minutes=21-i), D(100), D('99.75'), D(100)) for i in range(21))
    h = FuturesHistory(c.symbol, bars+(Bar(now, D('100.25'), D(100), D('100.25')),))
    q = Quote(c.symbol, D('100.25'), D('100.50'), now, now)
    with TemporaryDirectory(prefix='atlas-futures-signal-') as root:
        ledger = FuturesPaper(Path(root)/'synthetic.sqlite3')
        # This callback approves only this synthetic demonstration; no market authority.
        controller = FuturesPaperController(ledger, settings=FuturesSignalSettings(), authorize=lambda *args: True)
        disabled = controller.step(c,h,{c.symbol:q},now=now)
        ledger.set_enabled(True)
        opened = controller.step(c,h,{c.symbol:q},now=now)
        p = opened['proposal'].plan
        ledger.set_enabled(False)
        later = now+timedelta(seconds=1)
        exit_q = Quote(c.symbol,p.target,p.target+c.tick,later,later)
        closed = controller.step(c,h,{c.symbol:exit_q},now=later)
        state = ledger.snapshot()
        passed = (disabled['entry']=='ENTRIES_DISABLED' and opened['entry']=='OPENED'
                  and closed['protection'].get(opened['proposal'].intent_id)=='TARGET'
                  and not state['positions'])
        print(json.dumps({'scope':'SYNTHETIC_FUTURES_SIGNAL_HANDOFF_NOT_STRATEGY_PERFORMANCE',
                          'passed':passed,'default_entry':disabled['entry'],'enabled_entry':opened['entry'],
                          'exit_with_entries_disabled':list(closed['protection'].values()),
                          'positions_remaining':len(state['positions']),'network_requests':0},indent=2))
        ledger.close()
        return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
