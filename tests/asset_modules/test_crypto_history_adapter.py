from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from app.asset_modules.crypto_history_adapter import adapt, snapshot
from app.asset_modules.crypto_worker import CryptoSignalWorker, _assess, _pack
from app.crypto_research.evidence import CryptoBarInterval, normalize_crypto_rows
from app.crypto_research.models import CryptoObservation, CryptoPair
from app.crypto_research.runtime import CryptoResearchRuntime
from tests.asset_modules.test_crypto_worker import NOW, SETTINGS, data, wait


def inputs(count=22):
    history,row=data()
    # Use the same actual normalization path as the provider research runtime.
    candles=tuple({'timestamp':(bar.end-timedelta(minutes=1)).isoformat(),
        'open':str(bar.close),'high':str(bar.high),'low':str(bar.low),'close':str(bar.close)} for bar in history.bars)
    result=normalize_crypto_rows(row.pair,candles,interval=CryptoBarInterval.M1,
        observed_at=NOW,decision_cutoff=NOW)
    assert not result.malformed_rows and not result.incomplete_rows
    quote=CryptoObservation(row.pair,NOW,D(101),row.bid,row.ask,None)
    return result.bars[-count:] if count else (),quote


def test_provider_normalization_builds_worker_frame_without_authority():
    bars,q=inputs();frame=adapt('BTC/USD',bars,q,now=NOW)
    assert frame.status=='READY' and frame.source_bar_count==frame.worker_bar_count==22
    assert frame.available_at==NOW and frame.quote is q
    assert frame.history.bars[-1].end==bars[-1].closed_at
    assert frame.report()['scope']=='READ_ONLY_CRYPTO_HISTORY_COVERAGE_NO_ORDERS'
    assert _assess(_pack(frame.history,frame.quote,NOW,SETTINGS))['reason']=='PROPOSED'


@pytest.mark.parametrize('case,status',[
    ('empty','NO_COMPLETED_PROVIDER_BARS'),('few','INSUFFICIENT_COMPLETED_HISTORY'),
    ('pair','BAR_OWNERSHIP_OR_INTERVAL_MISMATCH'),('interval','BAR_OWNERSHIP_OR_INTERVAL_MISMATCH'),
    ('future','BAR_NOT_YET_AVAILABLE'),('duplicate','BAR_ORDER_OR_DUPLICATE_ERROR'),
    ('reverse','BAR_ORDER_OR_DUPLICATE_ERROR'),('gap','HISTORY_NOT_CURRENT_OR_CONTIGUOUS'),
    ('old','HISTORY_NOT_CURRENT_OR_CONTIGUOUS'),('provider','MIXED_PROVIDER_HISTORY'),
    ('provider_symbol','MIXED_PROVIDER_HISTORY'),('quote_old','QUOTE_UNAVAILABLE'),
    ('quote_future','QUOTE_UNAVAILABLE'),('quote_pair','QUOTE_UNAVAILABLE'),('quote_none','QUOTE_UNAVAILABLE')])
def test_adapter_refuses_bad_provenance_history_and_quotes(case,status):
    bars,q=inputs();now=NOW
    if case=='empty':bars=()
    elif case=='few':bars=bars[-10:]
    elif case=='pair':bars=bars[:-1]+(replace(bars[-1],canonical_symbol='ETH/USD'),)
    elif case=='interval':
        b=bars[-1];bars=bars[:-1]+(replace(b,interval=CryptoBarInterval.M5,opened_at=b.closed_at-timedelta(minutes=5)),)
    elif case=='future':bars=bars[:-1]+(replace(bars[-1],observed_at=NOW+timedelta(seconds=1),decision_cutoff=NOW+timedelta(seconds=1)),)
    elif case=='duplicate':bars=bars[:-1]+(bars[-2],)
    elif case=='reverse':bars=tuple(reversed(bars))
    elif case=='gap':bars=(replace(bars[0],opened_at=bars[0].opened_at-timedelta(minutes=1),closed_at=bars[0].closed_at-timedelta(minutes=1)),)+bars[1:]
    elif case=='old':now+=timedelta(minutes=2)
    elif case=='provider':bars=bars[:-1]+(replace(bars[-1],provider='ANOTHER_FEED'),)
    elif case=='provider_symbol':bars=bars[:-1]+(replace(bars[-1],provider_symbol='OTHER'),)
    elif case=='quote_old':q=replace(q,timestamp=NOW-timedelta(seconds=3))
    elif case=='quote_future':q=replace(q,timestamp=NOW+timedelta(seconds=1))
    elif case=='quote_pair':q=replace(q,pair=CryptoPair('ETH','USD','ETHUSD'))
    else:q=None
    frame=adapt('BTC/USD',bars,q,now=now)
    assert frame.status==status and frame.history is None and frame.quote is None


def test_runtime_accessor_is_point_in_time_bounded_and_network_free(tmp_path):
    bars,q=inputs()
    runtime=CryptoResearchRuntime(path=tmp_path/'unused.jsonl')
    with runtime._lock:runtime._bar_history.add_many(bars)
    assert runtime.completed_bars('BTC/USD',as_of=NOW-timedelta(microseconds=1))==()
    assert runtime.completed_bars('BTC/USD',as_of=NOW,limit=5)==bars[-5:]
    assert runtime.completed_bars('BTC/USD',as_of=NOW,interval=CryptoBarInterval.M5)==()
    assert runtime.metrics().history_requests_attempted==0
    assert not (tmp_path/'unused.jsonl').exists()
    # Source bars are immutable evidence; the adapter does not rewrite observation time.
    assert runtime.completed_bars('BTC/USD',as_of=NOW)[0].observed_at==NOW
    runtime.close()


@pytest.mark.parametrize('values',[{'limit':True},{'limit':65},{'interval':'M1'},
    {'as_of':NOW.replace(tzinfo=None)},{'symbol':''}])
def test_runtime_accessor_rejects_unbounded_or_ambiguous_requests(tmp_path,values):
    runtime=CryptoResearchRuntime(path=tmp_path/'unused.jsonl')
    params={'symbol':'BTC/USD','as_of':NOW,**values}
    with pytest.raises(ValueError):runtime.completed_bars(**params)
    runtime.close()


def test_real_runtime_decision_and_provider_history_are_read_only(tmp_path):
    bars,q=inputs()
    runtime=CryptoResearchRuntime(enabled=True,path=tmp_path/'observations.jsonl',clock=lambda:NOW)
    with runtime._lock:runtime._bar_history.add_many(bars)
    runtime.start()
    try:
        assert runtime.admit(q)
        import time
        deadline=time.monotonic()+3
        while not runtime.latest() and time.monotonic()<deadline:time.sleep(.01)
        before=runtime.metrics()
        frame=snapshot(runtime,'BTC/USD',now=NOW)
        assert frame.status=='READY' and frame.quote is runtime.latest()[0]
        assert runtime.metrics()==before
        worker=CryptoSignalWorker(timeout_seconds=10)
        try:
            worker.start();assert wait(worker)['kind']=='READY'
            worker.assess(frame.history,frame.quote,NOW,SETTINGS)
            assert wait(worker)['payload']['reason']=='PROPOSED'
            assert worker.status()['execution_enabled'] is False
        finally:worker.close()
    finally:runtime.close()


def test_bounded_conversion_retains_actual_latest_48_provider_bars():
    bars,q=inputs();first=bars[0]
    earlier=tuple(replace(first,opened_at=first.opened_at-timedelta(minutes=i),
        closed_at=first.closed_at-timedelta(minutes=i)) for i in range(42,0,-1))
    frame=adapt('BTC/USD',earlier+bars,q,now=NOW)
    assert frame.status=='READY' and frame.source_bar_count==64 and frame.worker_bar_count==48
    assert frame.history.bars[0].end==(earlier+bars)[-48].closed_at
    assert frame.history.bars[-1].end==bars[-1].closed_at


def test_adapter_refuses_quote_provider_alias_and_precompletion_observation():
    bars,q=inputs()
    alias=replace(q,pair=CryptoPair('BTC','USD','OTHERBTC'))
    assert adapt('BTC/USD',bars,alias,now=NOW).status=='QUOTE_UNAVAILABLE'
    unavailable=bars[:-1]+(replace(bars[-1],observed_at=bars[-1].opened_at),)
    assert adapt('BTC/USD',unavailable,q,now=NOW).status=='BAR_AVAILABILITY_TIMESTAMPS_INVALID'
