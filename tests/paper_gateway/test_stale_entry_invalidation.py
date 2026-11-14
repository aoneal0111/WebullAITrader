"""Queued quotes cannot cancel newer orders or stop the authoritative lane."""
from datetime import timedelta
from dataclasses import replace
from decimal import Decimal

from app.live_scanner.authoritative_lane import AuthoritativeEventLane
from app.paper_trading.command_composition import create_paper_trading_command_composition
from app.paper_trading.order_models import OrderStatus, OrderTerminalReason
from tests.paper_gateway.test_session_aware_entry_validity import (
    Clock, START, Signal, quote, submit_entry,
)


def test_old_structural_breach_does_not_cancel_new_entry_and_fresh_quote_fills(tmp_path):
    clock=Clock(START);events=[];path=tmp_path/'paper.sqlite3'
    c=create_paper_trading_command_composition(clock=clock,event_sink=events.append,persistence_path=str(path))
    try:
        _,order=submit_entry(c)
        baseline=len(events);clock.value=START+timedelta(milliseconds=200)
        old=quote(START-timedelta(milliseconds=70),bid='9.39',ask='9.40')
        assert c.gateway.process_market_event(old)==()
        unchanged=c.order_book.get(order.order_id)
        assert unchanged==order
        assert len(events)==baseline
        # Rejection is local to stale evidence; the next valid quote still trades.
        reports=c.gateway.process_market_event(quote(clock.value))
        assert reports[0].order.status is OrderStatus.FILLED
        assert reports[0].order.fills[0].timestamp==clock.value
        assert old.timestamp==START-timedelta(milliseconds=70)
    finally:c.close()
    restored=create_paper_trading_command_composition(clock=clock,persistence_path=str(path))
    try:assert restored.order_book.get(order.order_id).status is OrderStatus.FILLED
    finally:restored.close()


def test_current_structural_breach_still_cancels_including_equal_timestamp():
    for offset in (timedelta(0),timedelta(milliseconds=200)):
        clock=Clock(START);c=create_paper_trading_command_composition(clock=clock)
        try:
            _,order=submit_entry(c);clock.value=START+offset
            assert c.gateway.process_market_event(quote(clock.value,bid='9.39',ask='9.40'))==()
            result=c.order_book.get(order.order_id)
            assert result.status is OrderStatus.CANCELLED
            assert result.terminal_reason is OrderTerminalReason.STRUCTURAL_STOP_INVALIDATED
            assert result.updated_at==clock.value
        finally:c.close()


def test_one_queued_quote_can_invalidate_older_order_but_not_newer_order():
    clock=Clock(START);c=create_paper_trading_command_composition(clock=clock)
    try:
        _,first=submit_entry(c)
        clock.value=START+timedelta(milliseconds=200)
        second=replace(first,order_id='newer-entry',created_at=clock.value,updated_at=clock.value,
            request=replace(first.request,client_order_id='newer-entry',
                strategy_lifecycle_id=Signal().lifecycle_id.replace('14:00:00','14:00:01')))
        c.order_book.restore(second)
        event=quote(START+timedelta(milliseconds=100),bid='9.39',ask='9.40')
        c.gateway.process_market_event(event)
        assert c.order_book.get(first.order_id).status is OrderStatus.CANCELLED
        assert c.order_book.get(second.order_id)==second
    finally:c.close()


def test_quote_older_than_partial_fill_preserves_remaining_entry_and_fill():
    clock=Clock(START);c=create_paper_trading_command_composition(clock=clock)
    try:
        _,order=submit_entry(c);clock.value=START+timedelta(milliseconds=200)
        c.gateway.process_market_event(quote(clock.value,size='40'))
        partial=c.order_book.get(order.order_id)
        assert partial.status is OrderStatus.PARTIALLY_FILLED
        clock.value=START+timedelta(milliseconds=300)
        c.gateway.process_market_event(quote(START+timedelta(milliseconds=100),bid='9.39',ask='9.40'))
        assert c.order_book.get(order.order_id)==partial
        c.gateway.process_market_event(quote(clock.value,bid='9.39',ask='9.40'))
        cancelled=c.order_book.get(order.order_id)
        assert cancelled.status is OrderStatus.CANCELLED
        assert cancelled.filled_quantity==Decimal('40')
        assert cancelled.fills==partial.fills
    finally:c.close()


def test_authoritative_lane_survives_old_breach_then_processes_next_symbol():
    clock=Clock(START);c=create_paper_trading_command_composition(clock=clock)
    try:
        _,first=submit_entry(c)
        submit_entry(c,Signal(symbol='NEXT',lifecycle_id=Signal().lifecycle_id.replace('PMI','NEXT')))
        second=next(o for o in c.order_book.open_orders() if o.order_id!=first.order_id)
        clock.value=START+timedelta(milliseconds=200)
        lane=AuthoritativeEventLane(c.gateway.process_market_event)
        lane.start()
        lane.publish(quote(START-timedelta(milliseconds=70),bid='9.39',ask='9.40'))
        lane.publish(quote(clock.value,symbol='NEXT'))
        lane.stop(timeout=2)
        assert not lane.failed
        assert lane.metrics()['authoritative_events_dequeued']==2
        assert c.order_book.get(first.order_id)==first
        assert c.order_book.get(second.order_id).filled_quantity==Decimal('100')
    finally:c.close()
