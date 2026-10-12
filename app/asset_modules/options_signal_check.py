"""Fabricated offline signal handoff; not a strategy performance test."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.asset_modules.contract_logic import Bar, Contract, Quote
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.options_signals import OptionsHistory, OptionsPaperController, OptionsSignalSettings


def main():
    now = datetime(2026, 10, 12, 15, tzinfo=UTC)
    trips = []
    with TemporaryDirectory(prefix='atlas-options-signal-') as root:
        ledger = OptionsPaper(Path(root)/'synthetic.sqlite3')
        controller = OptionsPaperController(ledger, settings=OptionsSignalSettings(), authorize=lambda *a: True)
        for side, right in (('LONG','CALL'), ('SHORT','PUT')):
            at = now if side == 'LONG' else now+timedelta(minutes=2)
            bars = tuple(Bar(at-timedelta(minutes=21-i), D(100), D('99.75'), D(100)) for i in range(21))
            latest = Bar(at, D('100.25'), D(100), D('100.25'))
            uq = Quote('SPY', D('100.25'), D('100.26'), at, at)
            if side == 'SHORT':
                bars = tuple(Bar(b.end, D('100.25'), D(100), D(100)) for b in bars)
                latest = Bar(at, D(100), D('99.75'), D('99.75'))
                uq = Quote('SPY', D('99.74'), D('99.75'), at, at)
            h = OptionsHistory('SPY', bars+(latest,))
            c = Contract('SYNTHETIC_'+right, EngineId.OPTIONS, at+timedelta(days=30),
                         D(100), D('.01'), D(1), 'SPY', right, D(100))
            q = Quote(c.symbol, D('1.99'), D(2), at, at)
            disabled = controller.step(h,uq,(c,),{c.symbol:q},now=at)
            ledger.set_enabled(True)
            opened = controller.step(h,uq,(c,),{c.symbol:q},now=at)
            p = opened['proposal'].plan
            ledger.set_enabled(False)
            later = at+timedelta(seconds=1)
            target = Quote(c.symbol,p.target,p.target+c.tick,later,later)
            # Missing underlying history cannot prevent protection of an open position.
            closed = controller.step(OptionsHistory('SPY',()),None,(c,),{c.symbol:target},now=later)
            trips.append({'underlying_direction':side,'option_right':right,
                          'default_entry':disabled['entry'],'enabled_entry':opened['entry'],
                          'protection_with_entries_disabled':list(closed['protection'].values())})
        state = ledger.snapshot()
        passed = (all(t['default_entry']=='ENTRIES_DISABLED' and t['enabled_entry']=='OPENED'
                      and t['protection_with_entries_disabled']==['TARGET'] for t in trips)
                  and not state['positions'])
        print(json.dumps({'scope':'SYNTHETIC_OPTIONS_SIGNAL_HANDOFF_NOT_STRATEGY_PERFORMANCE',
                          'passed':passed,'round_trips':trips,'synthetic_net_pnl':state['realized_pnl'],
                          'positions_remaining':len(state['positions']),'network_requests':0},indent=2))
        ledger.close()
        return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
