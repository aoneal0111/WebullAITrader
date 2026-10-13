"""Offline synthetic provider normalization -> history adapter -> worker check."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
import time

from app.asset_modules.crypto_history_adapter import adapt
from app.asset_modules.crypto_signals import CryptoSignalSettings
from app.asset_modules.crypto_worker import CryptoSignalWorker
from app.crypto_research.evidence import CryptoBarInterval, normalize_crypto_rows
from app.crypto_research.models import CryptoObservation, CryptoPair


def main():
    now=datetime(2026,10,13,15,tzinfo=UTC)
    pair=CryptoPair('BTC','USD','BTCUSD')
    rows=[]
    for index in range(22):
        end=now-timedelta(seconds=1,minutes=21-index)
        rows.append({'timestamp':(end-timedelta(minutes=1)).isoformat(),
            'open':'100' if index<21 else '101', 'high':'100.2' if index<21 else '101.2',
            'low':'98' if index<21 else '100','close':'100' if index<21 else '101'})
    normalized=normalize_crypto_rows(pair,rows,interval=CryptoBarInterval.M1,observed_at=now,decision_cutoff=now)
    q=CryptoObservation(pair,now,D(101),D(101),D('101.1'),None)
    frame=adapt('BTC/USD',normalized.bars,q,now=now)
    worker=CryptoSignalWorker(timeout_seconds=10)
    def wait():
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            reply=worker.poll()
            if reply:return reply
            time.sleep(.01)
        raise RuntimeError('Crypto worker check timed out')
    try:
        if frame.status!='READY':raise ValueError('Synthetic provider frame not ready')
        worker.start()
        if wait()['kind']!='READY':raise ValueError('Worker not ready')
        worker.assess(frame.history,frame.quote,now,CryptoSignalSettings())
        response=wait()
        passed=response['kind']=='ASSESSED' and response['payload']['reason']=='PROPOSED'
        print(json.dumps({'scope':'SYNTHETIC_PROVIDER_BAR_ADAPTER_NOT_REAL_DATA_VALIDATION',
            'passed':passed,'history':frame.report(),'worker_assessment':response['payload']['reason'],
            'separate_process':True,'execution_enabled':worker.status()['execution_enabled'],
            'network_requests':0,'orders':0},indent=2))
        return 0 if passed else 1
    finally:worker.close()


if __name__=='__main__':raise SystemExit(main())
