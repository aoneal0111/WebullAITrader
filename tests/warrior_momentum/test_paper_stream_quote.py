from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
from dataclasses import replace
from app.market_data.models import MarketEvent,MarketEventType,QuotePayload,TradePayload
from app.strategies.warrior_momentum.paper_stream_quote import PaperStreamQuoteSource

NOW=datetime(2026,11,10,20,tzinfo=UTC)


def events(offset=0,source='webull'):
    stamp=NOW+timedelta(seconds=offset)
    return (MarketEvent(1,stamp,'ABC',source,MarketEventType.TRADE,TradePayload(D('5'),D(10),'trade'),received_timestamp=NOW),
        MarketEvent(2,stamp,'ABC',source,MarketEventType.QUOTE,QuotePayload(D('4.99'),D('5.01'),D(100),D(100)),received_timestamp=NOW))


def test_fresh_stream_preserves_original_times_and_makes_no_rest_calls():
    calls=[];s=PaperStreamQuoteSource(lambda symbol:calls.append(symbol),clock=lambda:NOW)
    for e in events(.13):s.observe_market_event(e)
    q=s('ABC')
    assert q.bid==D('4.99') and q.ask==D('5.01')
    assert q.bid_timestamp==NOW+timedelta(milliseconds=130)
    assert not calls and s.status()['counts']['stream_used']==1


def test_old_future_untrusted_missing_and_invalid_stream_fall_back_to_rest():
    for offset,provider in [(-5.001,'webull'),(.251,'webull'),(0,'untrusted')]:
        calls=[];s=PaperStreamQuoteSource(lambda symbol:calls.append(symbol) or 'REST',clock=lambda:NOW)
        for e in events(offset,provider):s.observe_market_event(e)
        assert s('ABC')=='REST' and calls==['ABC']
    for mode in ['missing','crossed','nonfinite','delayed']:
        calls=[];s=PaperStreamQuoteSource(lambda symbol:calls.append(symbol) or 'REST',clock=lambda:NOW)
        trade,book=events()
        if mode!='missing':s.observe_market_event(trade)
        if mode=='crossed':book=replace(book,payload=QuotePayload(D('6'),D('5'),D(1),D(1)))
        if mode=='nonfinite':book=replace(book,payload=QuotePayload(D('NaN'),D('5'),D(1),D(1)))
        if mode=='delayed':book=replace(book,received_timestamp=NOW-timedelta(seconds=6))
        s.observe_market_event(book)
        assert s('ABC')=='REST'


def test_out_of_order_quote_cannot_replace_fresh_book_and_stream_decision_is_not_rest_provenance():
    class Rest:
        def __init__(self):self.decisions=[]
        def __call__(self,symbol):return None
        def record_decision(self,**values):self.decisions.append(values)
    rest=Rest();s=PaperStreamQuoteSource(rest,clock=lambda:NOW)
    for e in events():s.observe_market_event(e)
    older=events(-1)[1];s.observe_market_event(older)
    assert s('ABC').bid_timestamp==NOW
    s.record_decision(outcome='ACCEPTED')
    assert not rest.decisions and s.status()['counts']['out_of_order_ignored']==1


def test_warrior_does_not_reuse_scalper_timing_or_hide_fresh_rest():
    from app.strategies.warrior_momentum.paper_stream_quote import WarriorPaperStreamQuoteSource
    # All these stream inputs satisfy the unchanged native Scalper selector,
    # but each would fail an existing Warrior validation gate.
    for book_offset,trade_offset in [(-3,-1),(-1,-30),(.13,-1),(-1,.13)]:
        calls=[]
        warrior=WarriorPaperStreamQuoteSource(lambda symbol:calls.append(symbol) or 'FRESH_REST',clock=lambda:NOW)
        scalper=PaperStreamQuoteSource(lambda symbol:'UNEXPECTED_REST',clock=lambda:NOW)
        trade=events(trade_offset)[0];book=events(book_offset)[1]
        for source in (warrior,scalper):
            source.observe_market_event(trade);source.observe_market_event(book)
        assert warrior('ABC')=='FRESH_REST' and calls==['ABC']
        assert scalper('ABC')!='UNEXPECTED_REST'


def test_warrior_fresh_stream_keeps_provider_times_and_avoids_rest():
    from app.strategies.warrior_momentum.paper_stream_quote import WarriorPaperStreamQuoteSource
    calls=[];source=WarriorPaperStreamQuoteSource(lambda s:calls.append(s),clock=lambda:NOW)
    for event in events(-1):source.observe_market_event(event)
    quote=source('ABC')
    assert quote.bid_timestamp==NOW-timedelta(seconds=1)
    assert quote.last_timestamp==quote.bid_timestamp
    assert source.observer_provenance(quote)=='WEBULL_STREAM'
    assert calls==[]


def test_warrior_volume_only_snapshot_cannot_refresh_an_old_trade():
    from app.strategies.warrior_momentum.paper_stream_quote import WarriorPaperStreamQuoteSource
    calls=[];source=WarriorPaperStreamQuoteSource(lambda s:calls.append(s) or 'REST',clock=lambda:NOW)
    source.observe_market_event(events(-30)[0])
    source.observe_market_event(events()[1])
    retained=replace(events()[0],payload=TradePayload(D('5'),D('1000'),'snapshot-retained-price'))
    source.observe_market_event(retained)
    assert source('ABC')=='REST' and calls==['ABC']
    source.observe_market_event(events()[0])
    assert source('ABC').last_timestamp==NOW
