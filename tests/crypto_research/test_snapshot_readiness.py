from dataclasses import replace
from datetime import timedelta

import pytest

from app.crypto_research import CryptoResearchRuntime,CryptoResearchStatus,CryptoResearchViewStore,CryptoPair
from tests.crypto_research.test_runtime import observation,MemoryStore,T0


def runtime(rows):
    pairs=tuple(CryptoPair(s,'USD',s+'USD') for s in ('BTC','ETH'))
    class Provider:
        def discover(self,_):return pairs
        def snapshots(self,_):return rows
    view=CryptoResearchViewStore()
    r=CryptoResearchRuntime(enabled=True,provider=Provider(),store=MemoryStore(),view_store=view,clock=lambda:T0)
    r._accepting=True
    return r,view


def test_future_quote_never_reaches_queue_and_has_separate_metric():
    r,_=runtime(())
    try:
        assert not r.admit(observation(at=T0+timedelta(seconds=1)))
        assert r.metrics().future_quotes==1 and r.metrics().crypto_queue_depth==0
        assert r.metrics().stale_quotes==0
    finally:r.close()


@pytest.mark.parametrize('rows,status,missing,fresh',[
    ((),'AWAITING_DATA',2,0),
    ((observation(),),'PARTIAL_DATA',1,1),
    ((observation(),observation('BTC')),'PARTIAL_DATA',1,1),
    ((observation(),observation('ETH',at=T0-timedelta(minutes=3))),'PARTIAL_DATA',0,1),
    ((observation(),observation('ETH',at=T0+timedelta(seconds=1))),'PARTIAL_DATA',0,1),
    ((observation(),replace(observation('ETH'),bid=None)),'PARTIAL_DATA',0,1),
    ((observation(),observation('ETH'),observation('DOGE')),'PARTIAL_DATA',0,2),
])
def test_successful_http_does_not_manufacture_full_fresh_coverage(rows,status,missing,fresh):
    r,view=runtime(rows)
    try:
        r.refresh_once();m=r.metrics()
        assert view.status_snapshot().status.value==status
        assert m.refreshes_complete==0 and m.refreshes_partial==1
        assert m.snapshot_symbols_missing==missing and m.snapshot_symbols_fresh==fresh
        if any(row.pair.base_asset=='DOGE' for row in rows):
            assert m.crypto_queue_depth==2 # unrequested DOGE never enters the queue
    finally:r.close()


def test_repeat_fresh_duplicates_are_healthy_and_queue_rejection_is_not():
    rows=(observation(),observation('ETH'));r,view=runtime(rows)
    try:
        assert r.refresh_once()==2 and r.refresh_once()==0
        assert r.metrics().refreshes_complete==2
        assert r.metrics().snapshot_symbols_fresh==4
        assert view.status_snapshot().status is CryptoResearchStatus.ACTIVE
    finally:r.close()
    r,view=runtime(rows);r._accepting=False
    try:
        assert r.refresh_once()==0
        assert r.metrics().refreshes_complete==0
        assert view.status_snapshot().status is CryptoResearchStatus.PARTIAL_DATA
    finally:r.close()
