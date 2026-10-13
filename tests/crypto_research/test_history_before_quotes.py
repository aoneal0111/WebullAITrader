from datetime import timedelta
from decimal import Decimal as D

from app.asset_modules.crypto_history_adapter import adapt
from app.crypto_research import CryptoPair, CryptoResearchRuntime, CryptoObservation
from app.crypto_research.bar_capture import CryptoBarCapture, read_bars
from app.crypto_research.evidence import CryptoBarInterval, normalize_crypto_rows
from tests.asset_modules.test_crypto_worker import NOW


def rows(end,*,breakout=False):
    return tuple({'timestamp':(end-timedelta(minutes=22-i)).isoformat(),
        'open':'101' if breakout and i==21 else '100',
        'high':'101.2' if breakout and i==21 else '100.2',
        'low':'100' if breakout and i==21 else '98',
        'close':'101' if breakout and i==21 else '100'} for i in range(22))


def test_new_history_is_captured_before_fresh_quote_cutoff_without_backdating(tmp_path):
    pair=CryptoPair('BTC','USD','BTCUSD')
    captured=CryptoBarCapture(tmp_path/'bars.db')
    prior=normalize_crypto_rows(pair,rows(NOW-timedelta(minutes=1)),interval=CryptoBarInterval.M1,
        observed_at=NOW-timedelta(minutes=1),decision_cutoff=NOW-timedelta(minutes=1)).bars
    captured.append_many(prior,captured_at=NOW-timedelta(minutes=1))
    clock=[NOW+timedelta(seconds=15)]
    old_cutoff=clock[0]
    old_quote=CryptoObservation(pair,old_cutoff,D(101),D(101),D('101.1'),None)
    original=read_bars(captured.path,'BTC/USD',as_of=old_cutoff)
    assert adapt('BTC/USD',original,old_quote,now=old_cutoff).status=='HISTORY_NOT_CURRENT_OR_CONTIGUOUS'
    events=[];records=[]
    class Store:
        def append(self,r):records.append(r)
        def close(self):pass
    class Provider:
        def discover(self,_):return (pair,)
        def historical_bars(self,p,*,timespan,**_):
            events.append(('HISTORY',timespan))
            clock[0]+=timedelta(seconds=1)
            return rows(NOW,breakout=True) if timespan=='M1' else ()
        def snapshots(self,_):
            events.append(('QUOTE',None))
            clock[0]+=timedelta(seconds=1)
            return (CryptoObservation(pair,clock[0]-timedelta(milliseconds=500),D(101),D(101),D('101.1'),None),)
    runtime=CryptoResearchRuntime(enabled=True,provider=Provider(),configured_pairs=(pair,),
        store=Store(),clock=lambda:clock[0],intelligence_context_sink=lambda _:None,
        completed_bar_capture=captured)
    runtime._research_shortlist=lambda:(pair,)
    runtime._accepting=True
    try:
        assert runtime.refresh_once()==1
        runtime._evaluate_and_persist(runtime._queue.get_nowait())
        decision=runtime.latest()[0]
        bars=read_bars(captured.path,'BTC/USD',as_of=decision.decision_cutoff)
        frame=adapt('BTC/USD',bars,decision,now=decision.decision_cutoff)
        assert events==[('HISTORY','M1'),('HISTORY','M5'),('QUOTE',None)]
        assert frame.status=='READY'
        assert decision.decision_cutoff-bars[-1].closed_at<timedelta(minutes=1)
        assert decision.decision_cutoff-decision.timestamp==timedelta(milliseconds=500)
        # Repair changes future acquisition, never the previously recorded view.
        assert read_bars(captured.path,'BTC/USD',as_of=old_cutoff)==original
        assert adapt('BTC/USD',original,old_quote,now=old_cutoff).status=='HISTORY_NOT_CURRENT_OR_CONTIGUOUS'
        assert runtime.metrics().history_requests_attempted==2
        assert runtime.metrics().snapshot_batches_requested==1
        assert records[0]['decision_cutoff']==decision.decision_cutoff.isoformat()
        assert records[0]['execution_authorized'] is False
    finally:runtime.close()


def test_history_provider_failures_do_not_suppress_snapshot_admission(tmp_path):
    pair=CryptoPair('BTC','USD','BTCUSD');events=[]
    class Provider:
        def discover(self,_):return (pair,)
        def historical_bars(self,*_,**kwargs):events.append('HISTORY');raise OSError('unavailable')
        def snapshots(self,_):
            events.append('QUOTE')
            return (CryptoObservation(pair,NOW,D(101),D(101),D('101.1'),None),)
    runtime=CryptoResearchRuntime(enabled=True,provider=Provider(),configured_pairs=(pair,),
        path=tmp_path/'quotes.jsonl',clock=lambda:NOW,intelligence_context_sink=lambda _:None)
    runtime._research_shortlist=lambda:(pair,);runtime._accepting=True
    try:
        assert runtime.refresh_once()==1
        assert events==['HISTORY','HISTORY','QUOTE']
        metrics=runtime.metrics()
        assert metrics.history_requests_failed==2 and metrics.snapshot_batches_succeeded==1
        assert metrics.provider_failures==0 and metrics.history_requests_attempted==2
    finally:runtime.close()


def test_disabled_history_bridge_adds_no_requests_and_quotes_still_admit(tmp_path):
    pair=CryptoPair('BTC','USD','BTCUSD')
    class Provider:
        def discover(self,_):return (pair,)
        def historical_bars(self,*a,**k):raise AssertionError('Disabled history requested')
        def snapshots(self,_):return (CryptoObservation(pair,NOW,D(101),D(101),D('101.1'),None),)
    runtime=CryptoResearchRuntime(enabled=True,provider=Provider(),configured_pairs=(pair,),
        path=tmp_path/'quotes.jsonl',clock=lambda:NOW)
    runtime._accepting=True
    try:
        assert runtime.refresh_once()==1
        assert runtime.metrics().history_requests_attempted==0
    finally:runtime.close()


def test_slow_history_acquisition_still_obeys_history_freshness_guard(tmp_path):
    pair=CryptoPair('BTC','USD','BTCUSD');clock=[NOW]
    class Provider:
        def discover(self,_):return (pair,)
        def historical_bars(self,p,*,timespan,**_):
            if timespan=='M1':return rows(NOW,breakout=True)
            clock[0]+=timedelta(seconds=90)
            return ()
        def snapshots(self,_):return (CryptoObservation(pair,clock[0],D(101),D(101),D('101.1'),None),)
    runtime=CryptoResearchRuntime(enabled=True,provider=Provider(),configured_pairs=(pair,),
        path=tmp_path/'quotes.jsonl',clock=lambda:clock[0],intelligence_context_sink=lambda _:None)
    runtime._research_shortlist=lambda:(pair,);runtime._accepting=True
    try:
        assert runtime.refresh_once()==1
        runtime._evaluate_and_persist(runtime._queue.get_nowait())
        decision=runtime.latest()[0]
        frame=adapt('BTC/USD',runtime.completed_bars('BTC/USD',as_of=clock[0]),decision,now=clock[0])
        assert frame.status=='HISTORY_NOT_CURRENT_OR_CONTIGUOUS'
        assert decision.timestamp==clock[0]
    finally:runtime.close()
