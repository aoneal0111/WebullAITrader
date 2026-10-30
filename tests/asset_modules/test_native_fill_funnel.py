from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from app.asset_modules.native_fill_funnel import NativeFillFunnel

NOW = datetime(2026, 10, 29, 18, tzinfo=UTC)


def fixture(owner='QUICK_SCALPER', side='BUY', stamp=NOW):
    fill = NS(request_id='fill-1', side=side, symbol='XYZ', quantity=D(2), fill_price=D(10), timestamp=stamp)
    order = NS(order_id='order-1', symbol='XYZ',
        request=NS(metadata={'strategy_owner': owner}, strategy_lifecycle_id='life', side=NS(value='BUY')),
        fills=[NS(fill_id='fill-1', quantity=D(2), price=D(10))])
    event = NS(event_type='ORDER_FILLED', fill=fill, order=NS(order_id='order-1'))
    return order, event


@pytest.mark.parametrize('owner', ['WARRIOR_MOMENTUM', 'QUICK_SCALPER'])
def test_canonical_fill_counted_once_with_owner(owner):
    order, event = fixture(owner); records=[]
    observer=NativeFillFunnel(lambda _:order, lambda **k:records.append(k), started_at=NOW)
    observer.observe(event); observer.observe(event)
    assert len(records)==1 and records[0]['stage']=='FILL'
    assert records[0]['engine']==('WARRIOR_MOMENTUM_V1' if owner=='WARRIOR_MOMENTUM' else owner)


def test_distinct_partial_and_final_fills_and_duplicate_notifications():
    order, event=fixture(); records=[]
    observer=NativeFillFunnel(lambda _:order, lambda **k:records.append(k), started_at=NOW)
    event.event_type='ORDER_PARTIALLY_FILLED';observer.observe(event);observer.observe(event)
    event.fill=NS(**{**vars(event.fill),'request_id':'fill-2'})
    order.fills.append(NS(fill_id='fill-2',quantity=D(2),price=D(10)))
    event.event_type='ORDER_FILLED';observer.observe(event)
    assert [r['stage'] for r in records]==['PARTIAL_FILL','FILL']


@pytest.mark.parametrize('change', ['old','sell','unknown','unpersisted','wrong_price','zero','canonical_sell','wrong_symbol'])
def test_replayed_exit_or_unverified_events_never_count(change):
    order,event=fixture();records=[]
    if change=='old':event.fill.timestamp=NOW-timedelta(seconds=1)
    if change=='sell':event.fill.side='SELL'
    if change=='unknown':order.request.metadata['strategy_owner']='OTHER'
    if change=='unpersisted':order.fills=[]
    if change=='wrong_price':event.fill.fill_price=D(11)
    if change=='zero':event.fill.quantity=D(0)
    if change=='canonical_sell':order.request.side.value='SELL'
    if change=='wrong_symbol':event.fill.symbol='OTHER'
    NativeFillFunnel(lambda _:order,lambda **k:records.append(k),started_at=NOW).observe(event)
    assert records==[]


def test_diagnostic_failure_does_not_escape():
    order,event=fixture()
    def fail(**_):raise RuntimeError('diagnostic failure')
    NativeFillFunnel(lambda _:order,fail,started_at=NOW).observe(event)
