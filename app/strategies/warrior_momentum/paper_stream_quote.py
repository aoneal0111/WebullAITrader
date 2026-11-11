"""Explicit PAPER-only confirmation from already subscribed Webull evidence.

Provider prices/timestamps are unchanged. Missing, stale, out-of-order or
untrusted stream data never replaces REST with an invented quote.
"""
from collections import OrderedDict,Counter
from contextvars import ContextVar
from datetime import UTC,datetime
from threading import RLock
from decimal import Decimal
from app.market_data.models import MarketEvent,MarketEventType,QuotePayload,TradePayload
from .execution_quote import ExecutionQuoteSnapshot


class PaperStreamQuoteSource:
    def __init__(self,rest,*,clock=lambda:datetime.now(UTC),future_tolerance=.25):
        if future_tolerance not in (0,.25):raise ValueError('Bounded PAPER quote timing required')
        self.rest,self.clock,self.future_tolerance=rest,clock,future_tolerance
        self._lock=RLock();self._symbols=OrderedDict();self._counts=Counter()
        self._last_source='NONE'
        self._selection=ContextVar('paper_quote_selection_'+str(id(self)),default='NONE')

    def observe_market_event(self,event):
        if (not isinstance(event,MarketEvent) or not isinstance(event.source,str) or event.source.lower()!='webull'
                or not isinstance(event.symbol,str) or not event.symbol
                or not isinstance(event.timestamp,datetime) or event.timestamp.tzinfo is None):return
        if event.event_type is MarketEventType.QUOTE and isinstance(event.payload,QuotePayload):key='book'
        elif event.event_type is MarketEventType.TRADE and isinstance(event.payload,TradePayload):key='trade'
        else:return
        received=event.received_timestamp or self.clock()
        if not isinstance(received,datetime) or received.tzinfo is None:return
        values=(event.payload.bid,event.payload.ask) if key=='book' else (event.payload.price,)
        valid=all(isinstance(v,Decimal) and v.is_finite() and v>0 for v in values)
        if key=='book' and valid:valid=event.payload.bid<=event.payload.ask
        with self._lock:
            state=self._symbols.setdefault(event.symbol,{})
            if not valid:
                state.pop(key,None);self._counts['invalid_stream_ignored']+=1;return
            old=state.get(key)
            if old is not None and event.timestamp<old[0].timestamp:
                self._counts['out_of_order_ignored']+=1;return
            state[key]=(event,received);self._symbols.move_to_end(event.symbol)
            while len(self._symbols)>512:self._symbols.popitem(last=False)

    def __call__(self,symbol):
        symbol=symbol.strip().upper();now=self.clock();chosen=None
        with self._lock:
            state=self._symbols.get(symbol,{})
            if 'book' in state and 'trade' in state:
                book,received=state['book'];trade,_=state['trade'];p=book.payload
                age=(now-book.timestamp).total_seconds();processing=(now-received).total_seconds()
                last_age=(now-trade.timestamp).total_seconds()
                if (all(v.is_finite() and v>0 for v in (p.bid,p.ask,trade.payload.price))
                        and p.bid<=p.ask and -self.future_tolerance<=age<=5
                        and 0<=processing<=5 and last_age>=-self.future_tolerance):
                    chosen=ExecutionQuoteSnapshot(symbol,trade.payload.price,p.bid,p.ask,
                        trade.timestamp,book.timestamp,book.timestamp,now)
            self._counts['stream_used' if chosen is not None else 'rest_requested']+=1
            self._last_source='WEBULL_STREAM' if chosen is not None else 'WEBULL_REST'
        self._selection.set('WEBULL_STREAM' if chosen is not None else 'WEBULL_REST')
        return chosen if chosen is not None else self.rest(symbol)

    def record_decision(self,**values):
        if self._selection.get()=='WEBULL_REST':
            callback=getattr(self.rest,'record_decision',None)
            if callable(callback):callback(**values)
        # Never attach a stream decision to an unrelated retained REST request.

    def status(self):
        with self._lock:return dict(source=self._last_source,counts=dict(self._counts))
