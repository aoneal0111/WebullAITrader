"""Synthetic margin and long/short arithmetic check; no market or broker calls."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.asset_modules.contract_logic import Contract, Quote, plan
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.futures_paper import FuturesPaper


def main():
    start=datetime(2026,10,12,15,tzinfo=UTC)
    contract=Contract('SYNTHETIC_MICRO',EngineId.FUTURES,start+timedelta(days=30),D(5),D('0.25'),D(1000))
    outcomes=[]
    with TemporaryDirectory(prefix='atlas-futures-mechanics-') as directory:
        paper=FuturesPaper(Path(directory)/'futures.db')
        try:
            paper.set_enabled(True)
            for n,side in enumerate(('LONG','SHORT')):
                now=start+timedelta(seconds=n*2)
                quote=Quote(contract.symbol,D(5000),D('5000.25'),now,now)
                p=plan(contract,quote,now,side=side,stop_distance=D(1),target_distance=D(3),
                    risk_budget=D(25),capital_budget=D(2500),fee_per_side=D(1),hold=timedelta(seconds=60))
                identity='synthetic-'+side
                entry=paper.enter(identity,p,quote,now=now)
                opened=paper.snapshot()
                later=now+timedelta(seconds=1)
                bid,ask=(D('5003.25'),D('5003.50')) if side=='LONG' else (D('4996.75'),D(4997))
                exit_quote=Quote(contract.symbol,bid,ask,later,later)
                exits=paper.protect({contract.symbol:exit_quote},now=later)
                outcomes.append({'side':side,'entry':entry,'exit':exits.get(identity),
                    'contracts':p.quantity,'reserved_margin':opened['reserved_margin']})
            state=paper.snapshot()
            passed=(all(r['entry']=='OPENED' and r['exit']=='TARGET' and D(r['reserved_margin'])==2000 for r in outcomes)
                and not state['positions'] and D(state['realized_pnl'])==42 and D(state['cash'])==10042)
            print(json.dumps({'scope':'SYNTHETIC_FUTURES_MECHANICS_NOT_STRATEGY_PERFORMANCE',
                'passed':passed,'round_trips':outcomes,'synthetic_net_pnl':state['realized_pnl'],
                'positions_remaining':len(state['positions']),'network_requests':0},indent=2))
            return 0 if passed else 1
        finally:paper.close()


if __name__=='__main__':
    raise SystemExit(main())
