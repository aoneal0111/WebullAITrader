"""Synthetic options mechanics check; no market connection or Atlas launch."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.asset_modules.contract_logic import Contract, Quote, option_plans
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.options_paper import OptionsPaper


def main():
    now = datetime(2026, 10, 12, 15, tzinfo=UTC)
    contract = Contract('SYNTHETIC_QQQ_CALL', EngineId.OPTIONS, now+timedelta(days=5),
        D(100), D('0.01'), D(1000), 'QQQ', 'CALL', D(500))
    quote = Quote(contract.symbol, D('1.00'), D('1.01'), now, now)
    plans = option_plans((contract,), {contract.symbol:quote}, 'QQQ', 'LONG', now,
        stop_distance=D('0.02'), target_distance=D('0.10'), risk_budget=D(25),
        capital_budget=D(500), fee_per_side=D('0.65'))
    with TemporaryDirectory(prefix='atlas-options-mechanics-') as directory:
        paper = OptionsPaper(Path(directory)/'options.sqlite3')
        try:
            paper.set_enabled(True)
            entry = paper.enter('synthetic-demo', plans[0], quote, now=now)
            later = now+timedelta(seconds=1)
            exit_quote = Quote(contract.symbol, D('1.11'), D('1.12'), later, later)
            exits = paper.protect({contract.symbol:exit_quote}, now=later)
            state = paper.snapshot()
            passed = (entry == 'OPENED' and exits == {'synthetic-demo':'TARGET'}
                and not state['positions'] and D(state['realized_pnl']) == D('26.80'))
            print(json.dumps({'scope':'SYNTHETIC_MECHANICS_NOT_STRATEGY_PERFORMANCE',
                'passed':passed, 'contracts':plans[0].quantity, 'entry_result':entry,
                'exit_result':exits, 'synthetic_net_pnl':state['realized_pnl'],
                'positions_remaining':len(state['positions']), 'network_requests':0}, indent=2))
            return 0 if passed else 1
        finally:
            paper.close()


if __name__ == '__main__':
    raise SystemExit(main())
