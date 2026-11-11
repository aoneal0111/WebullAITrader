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
