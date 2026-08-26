"""Paper-only adapters for order placement and cancellation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock

from app.momentum_scanner import AssetClass
from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
)
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeDecision,
    RuntimeEventSink,
)
from app.operations_core import OperationsOrder
from app.order_cancellation import (
    BrokerOrderCancellationAcknowledgement,
    OrderCancellationRequest,
)
from app.order_placement import (
    BrokerOrderAcknowledgement,
    NormalizedOrderStatus,
    OrderPlacementRequest,
)
from app.paper_trading.order_book import (
    DuplicateOrderError,
    OrderNotFoundError,
    PaperOrderBook,
)
from app.paper_trading.execution_engine import (
    ExecutionReport,
    PaperExecutionEngine,
)
from app.paper_trading.journal import append_event
from app.paper_trading.matching_engine import MarketQuote
from app.paper_trading.models import (
    JournalEventType,
    PaperFill,
    PaperJournal,
)
from app.paper_trading.order_models import (
    OrderRequest as PaperOrderRequest,
    OrderSide as PaperOrderSide,
    OrderStatus as PaperOrderStatus,
    OrderType as PaperOrderType,
    PaperOrder,
    TimeInForce as PaperTimeInForce,
)
from app.paper_trading.orders import (
    InvalidOrderTransitionError,
    accept_order,
    create_order,
)
from app.paper_gateway.durable_store import DurablePaperExecutionStore


Clock = Callable[[], datetime]
PositionAverageCostSource = Callable[[str], Decimal | None]
PositionQuantitySource = Callable[[str], Decimal]


def utc_now() -> datetime:
    return datetime.now(UTC)


class PaperOrderGateway:
    """Paper-only command, matching, journal, and runtime-event authority."""

    def __init__(
        self,
        order_book: PaperOrderBook,
        *,
        execution_engine: PaperExecutionEngine | None = None,
        event_sink: RuntimeEventSink | None = None,
        position_average_cost_source: (
            PositionAverageCostSource | None
        ) = None,
        position_quantity_source: PositionQuantitySource | None = None,
        clock: Clock = utc_now,
        source: str = "desktop-paper-execution",
        durable_store: DurablePaperExecutionStore | None = None,
    ) -> None:
        if not isinstance(order_book, PaperOrderBook):
            raise TypeError("order_book must be PaperOrderBook")
        if execution_engine is not None and (
            execution_engine.order_book is not order_book
        ):
            raise ValueError(
                "execution_engine must own the supplied order_book"
            )
        if event_sink is not None and not callable(event_sink):
            raise TypeError("event_sink must be callable or None")
        if (
            position_average_cost_source is not None
            and not callable(position_average_cost_source)
        ):
            raise TypeError(
                "position_average_cost_source must be callable or None"
            )
        if (
            position_quantity_source is not None
            and not callable(position_quantity_source)
        ):
            raise TypeError(
                "position_quantity_source must be callable or None"
            )
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be non-empty text")

        self._order_book = order_book
        self._execution_engine = (
            execution_engine
            if execution_engine is not None
            else PaperExecutionEngine(order_book)
        )
        self._event_sink = event_sink
        self._position_average_cost_source = (
            position_average_cost_source
        )
        self._position_quantity_source = position_quantity_source
        self._clock = clock
        self._source = source.strip()
        self._durable_store = durable_store
        self._sequence = 0
        self._journal = PaperJournal()
        self._lock = RLock()
        if self._durable_store is not None:
            for restored in self._durable_store.orders():
                self._order_book.restore(restored)
            self._sequence = max((event.sequence for event in self._durable_store.events()), default=0)
            if self._event_sink is not None:
                for event in self._durable_store.events():
                    self._event_sink(event)

    @property
    def order_book(self) -> PaperOrderBook:
        return self._order_book

    @property
    def execution_engine(self) -> PaperExecutionEngine:
        return self._execution_engine

    @property
    def journal(self) -> PaperJournal:
        with self._lock:
            return self._journal

    def place_order(
        self,
        request: OrderPlacementRequest,
    ) -> BrokerOrderAcknowledgement:
        if not isinstance(request, OrderPlacementRequest):
            raise TypeError(
                "request must be OrderPlacementRequest"
            )

        order = request.order
        with self._lock:
            if (
                order.side.value == "SELL"
                and self._position_quantity_source is not None
                and order.quantity
                > self._position_quantity_source(order.symbol)
            ):
                message = (
                    "paper sell quantity exceeds the projected long position"
                )
                self._append_journal(
                    JournalEventType.REJECTION,
                    order.request_id,
                    self._now(),
                    message,
                )
                self._publish_rejection(request, message)
                return BrokerOrderAcknowledgement(
                    client_order_id=order.client_order_id,
                    broker_order_id="",
                    accepted=False,
                    status=NormalizedOrderStatus.REJECTED,
                    message=message,
                    metadata={
                        "source": "paper_order_gateway",
                        "account_id": order.account_id,
                    },
                )
            if order.order_type.value not in {"MARKET", "LIMIT"}:
                message = (
                    "paper matching supports MARKET and LIMIT orders only"
                )
                self._append_journal(
                    JournalEventType.REJECTION,
                    order.request_id,
                    self._now(),
                    message,
                )
                self._publish_rejection(request, message)
                return BrokerOrderAcknowledgement(
                    client_order_id=order.client_order_id,
                    broker_order_id="",
                    accepted=False,
                    status=NormalizedOrderStatus.REJECTED,
                    message=message,
                    metadata={
                        "source": "paper_order_gateway",
                        "account_id": order.account_id,
                    },
                )
            duplicate = next(
                (
                    item
                    for item in self._order_book.history()
                    if item.request.client_order_id
                    == order.client_order_id
                ),
                None,
            )
            if duplicate is not None:
                message = "duplicate paper client order ID"
                self._append_journal(
                    JournalEventType.REJECTION,
                    order.request_id,
                    self._now(),
                    message,
                )
                self._publish_rejection(request, message)
                return BrokerOrderAcknowledgement(
                    client_order_id=order.client_order_id,
                    broker_order_id="",
                    accepted=False,
                    status=NormalizedOrderStatus.REJECTED,
                    message=message,
                    metadata={
                        "source": "paper_order_gateway",
                        "account_id": order.account_id,
                    },
                )

            paper_request = PaperOrderRequest(
                symbol=order.symbol,
                asset_class=AssetClass.STOCK,
                side=PaperOrderSide(order.side.value),
                order_type=PaperOrderType(order.order_type.value),
                quantity=order.quantity,
                time_in_force=PaperTimeInForce(
                    order.time_in_force.value
                ),
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                client_order_id=order.client_order_id,
            )

            paper_order = create_order(
                paper_request,
                clock=self._clock,
            )
            paper_order = accept_order(
                paper_order,
                at=paper_order.created_at,
            )
            try:
                report = self._execution_engine.submit(paper_order)
            except DuplicateOrderError:
                message = "duplicate paper order ID"
                self._append_journal(
                    JournalEventType.REJECTION,
                    order.request_id,
                    self._now(),
                    message,
                )
                self._publish_rejection(request, message)
                return BrokerOrderAcknowledgement(
                    client_order_id=order.client_order_id,
                    broker_order_id="",
                    accepted=False,
                    status=NormalizedOrderStatus.REJECTED,
                    message=message,
                    metadata={
                        "source": "paper_order_gateway",
                        "account_id": order.account_id,
                    },
                )

            self._append_journal(
                JournalEventType.PROPOSAL,
                order.request_id,
                paper_order.updated_at,
                report.message,
            )
            self._publish_order(
                paper_order,
                event_type="ORDER_ACCEPTED",
                message="Paper order accepted.",
                decision=RuntimeDecision(
                    decision_id=order.request_id,
                    timestamp=paper_order.updated_at,
                    strategy_id="operator-order-entry",
                    symbol=order.symbol,
                    action=order.side.value,
                    confidence=100,
                    reasoning_summary=(
                        "Operator submitted a validated paper order."
                    ),
                    risk_assessment=(
                        "Authenticated paper session and placement "
                        "policy approved."
                    ),
                    requested_quantity=order.quantity,
                    resulting_order_id=paper_order.order_id,
                ),
            )
            self._publish_order(
                paper_order,
                event_type="ORDER_WORKING",
                message="Paper order is working.",
                status="WORKING",
            )

        return BrokerOrderAcknowledgement(
            client_order_id=order.client_order_id,
            broker_order_id=paper_order.order_id,
            accepted=True,
            status=NormalizedOrderStatus.SUBMITTED,
            message="paper order accepted",
            metadata={
                "source": "paper_order_gateway",
                "account_id": order.account_id,
            },
        )

    def cancel_order(
        self,
        request: OrderCancellationRequest,
    ) -> BrokerOrderCancellationAcknowledgement | None:
        if not isinstance(request, OrderCancellationRequest):
            raise TypeError(
                "request must be OrderCancellationRequest"
            )

        try:
            existing = self._order_book.get(
                request.broker_order_id
            )
        except OrderNotFoundError:
            return None

        if (
            request.client_order_id is not None
            and existing.request.client_order_id
            != request.client_order_id
        ):
            return BrokerOrderCancellationAcknowledgement(
                broker_order_id=request.broker_order_id,
                client_order_id=existing.request.client_order_id,
                accepted=False,
                message="client order ID does not match",
                metadata={
                    "source": "paper_order_gateway",
                },
            )

        try:
            with self._lock:
                report = self._execution_engine.cancel(
                    request.broker_order_id,
                    at=self._now(),
                )
                self._append_journal(
                    JournalEventType.CANCELLATION,
                    request.request_id,
                    report.order.updated_at,
                    report.message,
                )
                self._publish_order(
                    report.order,
                    event_type="ORDER_CANCELLED",
                    message=report.message,
                )
        except InvalidOrderTransitionError:
            return BrokerOrderCancellationAcknowledgement(
                broker_order_id=request.broker_order_id,
                client_order_id=(
                    existing.request.client_order_id
                ),
                accepted=False,
                message="paper order cannot be cancelled",
                metadata={
                    "source": "paper_order_gateway",
                },
            )

        return BrokerOrderCancellationAcknowledgement(
            broker_order_id=report.order.order_id,
            client_order_id=(
                report.order.request.client_order_id
            ),
            accepted=True,
            message="paper order cancelled",
            metadata={
                "source": "paper_order_gateway",
            },
        )

    def process_market_event(
        self,
        event: MarketEvent,
    ) -> tuple[ExecutionReport, ...]:
        """Match open paper orders from the existing live quote boundary."""

        if not isinstance(event, MarketEvent):
            raise TypeError("event must be a MarketEvent")
        if event.event_type is not MarketEventType.QUOTE:
            return ()
        if event.symbol is None or not isinstance(
            event.payload,
            QuotePayload,
        ):
            raise TypeError(
                "QUOTE event requires a symbol and QuotePayload"
            )

        payload = event.payload
        if payload.bid <= 0 or payload.ask <= 0:
            return ()
        available_volume = min(
            payload.bid_size,
            payload.ask_size,
        )
        quote = MarketQuote(
            symbol=event.symbol,
            bid_price=payload.bid,
            ask_price=payload.ask,
            available_volume=available_volume,
            timestamp=event.timestamp,
            last_trade_price=None,
        )

        with self._lock:
            reports = self._execution_engine.process_quote(quote)
            for report in reports:
                if not report.fills:
                    continue
                fill = report.fills[0]
                realized_pnl = self._realized_pnl(
                    report.order,
                    fill.price,
                    fill.quantity,
                )
                runtime_fill = PaperFill(
                    request_id=fill.fill_id,
                    symbol=report.order.symbol,
                    side=report.order.side.value,
                    quantity=fill.quantity,
                    fill_price=fill.price,
                    notional=fill.notional,
                    realized_pnl=realized_pnl,
                    timestamp=fill.timestamp,
                )
                self._append_journal(
                    JournalEventType.FILL,
                    report.order.order_id,
                    fill.timestamp,
                    report.message,
                    (
                        ("fill_id", fill.fill_id),
                        ("quantity", format(fill.quantity, "f")),
                        ("price", format(fill.price, "f")),
                    ),
                )
                self._publish_order(
                    report.order,
                    event_type=(
                        "ORDER_FILLED"
                        if report.order.status
                        is PaperOrderStatus.FILLED
                        else "ORDER_PARTIALLY_FILLED"
                    ),
                    message=report.message,
                    fill=runtime_fill,
                    mark_price=fill.price,
                )
            return reports

    def _realized_pnl(
        self,
        order: PaperOrder,
        fill_price: Decimal,
        quantity: Decimal,
    ) -> Decimal:
        if order.side is PaperOrderSide.BUY:
            return Decimal("0")
        if self._position_average_cost_source is None:
            return Decimal("0")
        average_cost = self._position_average_cost_source(order.symbol)
        if average_cost is None:
            return Decimal("0")
        return (fill_price - average_cost) * quantity

    def _publish_rejection(
        self,
        request: OrderPlacementRequest,
        message: str,
    ) -> None:
        timestamp = self._now()
        order = request.order
        self._publish(
            PaperRuntimeEvent(
                sequence=self._next_sequence(),
                timestamp=timestamp,
                event_type="ORDER_REJECTED",
                message=message,
                cycle=0,
                symbol=order.symbol,
                source=self._source,
                order=OperationsOrder(
                    order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side.value,
                    quantity=format(order.quantity, "f"),
                    status="REJECTED",
                    updated_at=timestamp,
                ),
            )
        )

    def _publish_order(
        self,
        order: PaperOrder,
        *,
        event_type: str,
        message: str,
        status: str | None = None,
        fill: PaperFill | None = None,
        mark_price: Decimal | None = None,
        decision: RuntimeDecision | None = None,
    ) -> None:
        self._publish(
            PaperRuntimeEvent(
                sequence=self._next_sequence(),
                timestamp=order.updated_at,
                event_type=event_type,
                message=message,
                cycle=0,
                symbol=order.symbol,
                source=self._source,
                order=OperationsOrder(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side.value,
                    quantity=format(order.quantity, "f"),
                    status=status or order.status.value,
                    updated_at=order.updated_at,
                ),
                fill=fill,
                mark_price=mark_price,
                decision=decision,
            )
        )

    def _append_journal(
        self,
        event_type: JournalEventType,
        request_id: str,
        timestamp: datetime,
        message: str,
        details: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._journal = append_event(
            self._journal,
            event_type,
            request_id,
            timestamp,
            message,
            details,
        )

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _publish(self, event: PaperRuntimeEvent) -> None:
        if self._durable_store is not None:
            order = None
            if event.order is not None:
                try:
                    order = self._order_book.get(event.order.order_id)
                except OrderNotFoundError:
                    order = None
            self._durable_store.persist(event, order)
        if self._event_sink is not None:
            self._event_sink(event)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("paper gateway clock must be timezone-aware")
        return value


__all__ = ["PaperOrderGateway", "utc_now"]
