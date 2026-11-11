"""Mechanical protection check on a private copy of a closed PAPER ledger.

No provider, model or production order requests. Simulated SELL orders in the
isolated copy are reported separately; no profit or live protection is claimed.
"""
import argparse
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile
from app.paper_trading.command_composition import create_paper_trading_command_composition
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge,AutonomousPaperReadiness
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig
from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperPaperRuntimeAdapter
from app.equity_overhaul.paper_experiment import PaperExperiment


def check(review,output):
    review,output=Path(review),Path(output)
    if review.is_symlink() or review.stat().st_size>128*1024**2 or output.exists():
        raise ValueError('BOUNDED_REVIEW_AND_NEW_PRIVATE_OUTPUT_REQUIRED')
    with zipfile.ZipFile(review) as archive:
        infos=archive.infolist()
        if len(infos)>200 or len(infos)!=len({i.filename for i in infos}) or sum(i.file_size for i in infos)>600*1024**2:
            raise ValueError('REVIEW_ARCHIVE_BOUND')
        if archive.getinfo('paper-day-review.json').file_size>8*1024**2:raise ValueError('REVIEW_REPORT_BOUND')
        report=json.loads(archive.read('paper-day-review.json'))
        if report.get('scope')!='PRIVATE_CLOSED_PAPER_DAY_REVIEW_V1' or not report.get('capture',{}).get('closed'):
            raise ValueError('CLOSED_REVIEW_REQUIRED')
        for name,info in report['files'].items():
            if archive.getinfo(name).file_size!=info['bytes'] or sha256(archive.read(name)).hexdigest()!=info['sha256']:
                raise ValueError('REVIEW_FILE_CHANGED')
        spec=json.loads(archive.read('trial/experiment.json'))
        if spec['trial_id']!=report['trial_id'] or spec.get('profile')!='GROK_WARRIOR_ACCOUNT_V6':
            raise ValueError('ACCOUNT_PAPER_REVIEW_REQUIRED')
        output.mkdir(parents=True)
        ledger=output/'isolated-recovery.sqlite3';ledger.write_bytes(archive.read('native/paper-ledger.sqlite3'))
    clock=datetime.fromisoformat(report['collected_at']);holder={};lag=[False]
    def quantity(symbol):
        if lag[0]:return Decimal(0)
        return sum((o.filled_quantity*(1 if o.side.value=='BUY' else -1) for o in holder['commands'].order_book.history() if o.symbol==symbol),Decimal(0))
    commands=create_paper_trading_command_composition(persistence_path=str(ledger),clock=lambda:clock,position_quantity_source=quantity)
    holder['commands']=commands
    try:
        before=len(commands.order_book.history())
        bridge=AutonomousPaperExecutionBridge(commands.trading_service,commands.order_command_factory,
            order_book=commands.order_book,durable_store=commands.durable_store,
            protection_amender=commands.gateway.amend_protective_stop,position_quantity_source=quantity,
            management_context_source=lambda _: 'NON_OWNING_WARRIOR_ANALYTIC_CONTEXT')
        if bridge.reconcile() is not AutonomousPaperReadiness.READY:raise ValueError('COPY_RECONCILIATION_REFUSED')
        config=PaperExperiment.scalper_config(SimpleNamespace(cent_early=True,spec=spec),QuickScalperConfig(enabled=True))
        lag[0]=True  # Exercise the exact delayed-position-projection failure boundary.
        runtime=QuickScalperPaperRuntimeAdapter(config=config,bridge=bridge,order_book=commands.order_book,
            position_quantity_source=quantity,account_context_source=lambda:None,execution_quote_source=lambda _:None,clock=lambda:clock)
        checked=[]
        for symbol,state in runtime._positions.items():
            orders=commands.order_book.history()
            remaining=sum((o.filled_quantity*(1 if o.side.value=='BUY' else -1) for o in orders if o.request.strategy_lifecycle_id==state.lifecycle_id),Decimal(0))
            stops=[o for o in commands.order_book.open_orders_for_symbol(symbol) if o.side.value=='SELL' and o.request.order_type.value=='STOP' and o.request.strategy_lifecycle_id==state.lifecycle_id]
            if len(stops)!=1 or stops[0].remaining_quantity!=remaining:raise ValueError('COPY_PROTECTION_NOT_COMPLETE')
            checked.append(dict(symbol=symbol,lifecycle=state.lifecycle_id,open_quantity=str(remaining),
                protected_quantity=str(stops[0].remaining_quantity),stop_price=str(stops[0].request.stop_price)))
        result=dict(scope='ISOLATED_PAPER_MANAGEMENT_CHECK_V1',status='RECOVERY_CHECK_PASSED',trial_id=spec['trial_id'],
            review_sha256=sha256(review.read_bytes()).hexdigest(),positions=checked,
            isolated_simulated_orders_created=len(commands.order_book.history())-before,
            production_orders=0,network_requests=0,production_ledger_modified=False,
            limitation='Only native Scalper lifecycles in the private copy are checked/protected here. Runtime recovery on the workstation still requires normal manual launch; profitability is not established.')
    finally:commands.close()
    (output/'recovery-check.json').write_text(json.dumps(result,indent=2)+'\n')
    print('RECOVERY_CHECK=PASSED; PRODUCTION_ORDERS=0; NETWORK_REQUESTS=0')
    print('UPLOAD_SUMMARY='+str(output/'recovery-check.json'))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();check(args.review,args.output)


if __name__=='__main__':main()
