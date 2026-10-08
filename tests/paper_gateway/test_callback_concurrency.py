"""PAPER callbacks must not hold the mutation lock or reorder committed events."""
from dataclasses import replace
from decimal import Decimal
from threading import Event, Thread

import pytest

from app.composition.runtime_projection_pipeline import create_runtime_projection_pipeline
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.operations_core import OperationsBus
from app.order_cancellation import OrderCancellationRequest
from app.paper_gateway import PaperOrderGateway
from app.paper_trading.order_book import PaperOrderBook
from tests.paper_gateway.test_gateway import NOW, placement_request
from tests.warrior_momentum.test_quick_scalper_runtime import adapter


def request(number, symbol="AAPL"):
    original = placement_request()
    return replace(original, order=replace(original.order,
                   request_id=f"request-{number}", client_order_id=f"client-{number}",
                   symbol=symbol))


def cancel(gateway, acknowledgement):
    return gateway.cancel_order(OrderCancellationRequest(
        request_id="cancel", session_id="session-1", account_id="paper-account",
        broker_order_id=acknowledgement.broker_order_id,
        client_order_id=acknowledgement.client_order_id,
    ))


def test_fill_callback_and_strategy_mutation_do_not_invert_locks():
    pipeline = create_runtime_projection_pipeline(
        operations_bus=OperationsBus(), account_id="paper-account",
        paper_account_starting_cash=Decimal("1000"),
    )
    book = PaperOrderBook()
    runtime, _, _ = adapter(book=book)
    strategy_lock = runtime._lock
    strategy_ready, callback_entered = Event(), Event()
    errors, delivered = [], []

    def sink(event):
        delivered.append(event.sequence)
        pipeline.sink(event)
        if event.fill is not None:
            callback_entered.set()
            # Bound the old deadlock so regression failures finish cleanly.
            acquired = strategy_lock.acquire(timeout=3)
            try:
                assert acquired, "callback waited for strategy holding gateway lock"
                runtime.observe_paper_event(event)
            finally:
                if acquired:
                    strategy_lock.release()

    gateway = PaperOrderGateway(book, clock=lambda: NOW, event_sink=sink)
    gateway.place_order(request(1))
    second = gateway.place_order(request(2, "MSFT"))

    def strategy():
        try:
            with strategy_lock:
                strategy_ready.set()
                assert callback_entered.wait(3)
                assert cancel(gateway, second).accepted
        except BaseException as exc:
            errors.append(exc)

    def matching():
        try:
            assert strategy_ready.wait(3)
            gateway.process_market_event(MarketEvent(
                1, NOW, "AAPL", "callback-test", MarketEventType.QUOTE,
                QuotePayload(Decimal("4.99"), Decimal("5"), Decimal("10"), Decimal("10")),
            ))
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=strategy, daemon=True), Thread(target=matching, daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert delivered == sorted(set(delivered))
    assert book.get(second.broker_order_id).is_terminal
    positions = pipeline.position_projection.snapshot.positions
    assert [(p.symbol, Decimal(p.quantity)) for p in positions] == [("AAPL", Decimal("10"))]
    assert pipeline.paper_account_projection.snapshot.current_cash == Decimal("950")
    assert not gateway._pending_events


def test_reentrant_submission_delivers_current_batch_before_new_events():
    delivered = []
    submitted = False

    def sink(event):
        nonlocal submitted
        delivered.append(event.sequence)
        if not submitted:
            submitted = True
            assert gateway.place_order(request(2)).accepted

    gateway = PaperOrderGateway(PaperOrderBook(), clock=lambda: NOW, event_sink=sink)
    assert gateway.place_order(request(1)).accepted
    assert len(gateway.order_book.history()) == 2
    assert delivered == sorted(set(delivered))
    assert not gateway._pending_events


def test_observer_exception_releases_dispatcher_for_subsequent_events():
    delivered = []
    fail = True

    def sink(event):
        nonlocal fail
        if fail:
            fail = False
            raise RuntimeError("observer failed")
        delivered.append(event.sequence)

    gateway = PaperOrderGateway(PaperOrderBook(), clock=lambda: NOW, event_sink=sink)
    with pytest.raises(RuntimeError, match="observer failed"):
        gateway.place_order(request(1))
    assert gateway.place_order(request(2)).accepted
    assert delivered == sorted(set(delivered))
    assert not gateway._pending_events
    assert not gateway._dispatching_events
