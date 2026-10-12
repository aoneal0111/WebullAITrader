"""Synthetic spot/shared-allocation mechanics; no network, broker or AI."""
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.asset_modules.admission_controller import EntryReservation,PaperAdmissionController
from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.engine_catalog import EngineId
from app.crypto_research.models import CryptoObservation,CryptoPair


def main():
    now=datetime(2026,10,12,15,tzinfo=UTC)
    q=CryptoObservation(CryptoPair('BTC','USD','BTCUSD'),now,D(100),D('99.9'),D(100),D(10000))
    proposal={'id':'synthetic-entry','symbol':'BTC/USD','action':'BUY','notional':'200','stop':'98','target':'108'}
    with TemporaryDirectory(prefix='atlas-crypto-allocation-') as root:
        shared=PaperAdmissionController(Path(root)/'shared.sqlite3')
        shared.configure_account('synthetic-pool',capital=D(1000),loss_budget=D(50))
        equity=EntryReservation('synthetic-equity','synthetic-pool',EngineId.WARRIOR,
            'WARRIOR_MOMENTUM_V1|SPY|synthetic-equity','SPY','synthetic-v1','BUY',D(8),D(100),
            D('99.90'),D(1),D(800),D(10),now,now+timedelta(seconds=30))
        ledger=CryptoPaper(Path(root)/'crypto.sqlite3')
        router=CryptoAllocationRouter(shared,ledger,account_id='synthetic-pool',authorize=lambda *a:True)
        try:
            disabled=router.submit(proposal,{'BTC/USD':q},now=now)
            shared.reserve(equity,now=now);router.set_enabled(True)
            blocked=router.submit(proposal,{'BTC/USD':q},now=now)
            shared.abandon_unsubmitted(equity.command_id,equity.engine)
            opened=router.submit(proposal,{'BTC/USD':q},now=now)
            held=shared.snapshot('synthetic-pool')['reserved_capital']
            router.set_enabled(False)
            later=now+timedelta(seconds=1)
            closing=CryptoObservation(q.pair,later,D(110),D(110),D(111),D(10000))
            router.protect({'BTC/USD':closing},now=later)
            final=shared.snapshot('synthetic-pool');balance=shared.allocation_balance('synthetic-pool')
            evidence=ledger.intent_evidence(proposal['id'])
            passed=(disabled['entry']=='ENTRIES_DISABLED' and blocked['entry']=='SHARED_CAPITAL_LIMIT'
                and opened['entry']=='OPENED' and evidence['state']=='CLOSED'
                and not final['active_commands'] and balance['capital_ceiling']==D(1000))
            print(json.dumps({'scope':'SYNTHETIC_CRYPTO_SHARED_ALLOCATION_NOT_STRATEGY_PERFORMANCE',
                'passed':passed,'default_entry':disabled['entry'],'with_equity_reserved':blocked['entry'],
                'after_equity_released':opened['entry'],'crypto_reserved_capital':str(held),
                'exit_with_entries_disabled':evidence['state'],'shared_active_commands':final['active_commands'],
                'shared_reserved_capital':str(final['reserved_capital']),'capital_ceiling':str(balance['capital_ceiling']),
                'synthetic_crypto_realized_pnl':str(balance['crypto_realized_pnl']),'network_requests':0},indent=2))
            return 0 if passed else 1
        finally: ledger.close()


if __name__=='__main__':
    raise SystemExit(main())
