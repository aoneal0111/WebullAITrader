"""Offline synthetic crypto subprocess, shared allocation and protection check."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import time

from app.asset_modules.admission_controller import PaperAdmissionController
from app.asset_modules.contract_logic import Bar
from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.crypto_signals import CryptoHistory, CryptoSignalSettings
from app.asset_modules.crypto_worker import CryptoSignalWorker, CryptoWorkerController
from app.crypto_research.models import CryptoPair


def main():
    now=datetime(2026,10,13,15,tzinfo=UTC)
    bars=tuple(Bar(now-timedelta(seconds=1,minutes=21-i),D('100.2'),D(98),D(100)) for i in range(21))
    bars+=(Bar(now-timedelta(seconds=1),D('101.2'),D(100),D(101)),)
    history=CryptoHistory('BTC/USD',bars)
    q=SimpleNamespace(pair=CryptoPair('BTC','USD','BTCUSD'),bid=D(101),ask=D('101.1'),timestamp=now)
    quotes={'BTC/USD':q}
    with TemporaryDirectory(prefix='atlas-crypto-worker-') as directory:
        shared=PaperAdmissionController(Path(directory)/'shared.db')
        shared.configure_account('synthetic',capital=D(1000),loss_budget=D(50))
        ledger=CryptoPaper(Path(directory)/'crypto.db')
        worker=CryptoSignalWorker(timeout_seconds=10)
        router=CryptoAllocationRouter(shared,ledger,account_id='synthetic',authorize=lambda *a:True)
        parent=CryptoWorkerController(worker,router,settings=CryptoSignalSettings())
        def wait_ready():
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                reply=worker.poll()
                if reply and reply['kind']=='READY':return
                time.sleep(.01)
            raise RuntimeError('Worker startup timeout')
        def complete():
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                result=parent.poll(quotes,now=now)
                if result['entry']!='PENDING_WORKER':return result['entry']
                time.sleep(.01)
            raise RuntimeError('Worker completion timeout')
        try:
            worker.start();wait_ready()
            parent.submit(history,quotes,now=now);disabled=complete()
            router.set_enabled(True)
            parent.submit(history,quotes,now=now);enabled=complete()
            reserved=shared.snapshot('synthetic')['reserved_capital']
            parent.close()
            now+=timedelta(seconds=1)
            q.timestamp,q.bid,q.ask=now,D(115),D('115.1')
            result=parent.poll(quotes,now=now)
            balance=shared.snapshot('synthetic')
            passed=(disabled=='ENTRIES_DISABLED' and enabled=='OPENED' and reserved>0
                and not ledger.snapshot()['positions'] and balance['active_commands']==0)
            print(json.dumps({'scope':'SYNTHETIC_CRYPTO_WORKER_HANDOFF_NOT_STRATEGY_PERFORMANCE',
                'passed':passed,'separate_process':True,'default_entry':disabled,'enabled_entry':enabled,
                'reserved_capital':str(reserved),'protection_problems':result['protection'],
                'exit_after_worker_closed':ledger.snapshot()['events'][0]['reason'],
                'positions_remaining':len(ledger.snapshot()['positions']),
                'shared_active_commands':balance['active_commands'],
                'synthetic_realized_pnl':ledger.snapshot()['realized'],'network_requests':0},indent=2))
            return 0 if passed else 1
        finally:parent.close();ledger.close()


if __name__=='__main__':raise SystemExit(main())
