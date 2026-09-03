"""Paper-only adapters for order placement and cancellation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
    RuntimeHealthUpdate,
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
    OrderTerminalReason,
    OrderType as PaperOrderType,
    PaperOrder,
    TimeInForce as PaperTimeInForce,
)
from app.paper_trading.orders import (
    InvalidOrderTransitionError,
    accept_order,
    cancel_order as transition_cancel_order,
    create_order,
    expire_order as transition_expire_order,
)
from app.paper_gateway.durable_store import DurablePaperExecutionStore
from app.paper_gateway.order_validity import temporal_terminal_reason
from app.performance_diagnostics import performance_diagnostics


Clock = Callable[[], datetime]
PositionAverageCostSource = Callable[[str], Decimal | None]
PositionQuantitySource = Callable[[str], Decimal]
DEFAULT_MAXIMUM_PROCESSING_AGE_SECONDS = Decimal("5")
_LOGGER = logging.getLogger("atlas.paper_gateway")


class PaperDurabilityError(RuntimeError):
    """The PAPER authority is fail-closed after an authoritative write fails."""


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
        maximum_processing_age_seconds: Decimal = (
            DEFAULT_MAXIMUM_PROCESSING_AGE_SECONDS
        ),
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
        if maximum_processing_age_seconds < 0:
            raise ValueError("maximum processing age cannot be negative")

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
        self._maximum_processing_age_seconds = Decimal(
            maximum_processing_age_seconds
        )
        self._sequence = 0
        self._journal = PaperJournal()
        self._lock = RLock()
        self._durability_error: Exception | None = None
        if self._durable_store is not None:
            restored_events = self._durable_store.events()
            for restored in self._durable_store.orders():
                self._order_book.restore(restored)
            self._sequence = max((event.sequence for event in restored_events), default=0)
            if self._event_sink is not None:
                for event in restored_events:
                    self._event_sink(event)
            # Startup validity reconciliation is part of gateway construction,
            # before the command graph can expose matching or autonomous entry.
            self.reconcile_temporal_validity()

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

    @property
    def durability_failed(self) -> bool:
        with self._lock:
            return self._durability_error is not None

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
            self._require_durability()
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
            if order.order_type.value not in {"MARKET", "LIMIT", "STOP"}:
                message = (
                    "paper matching supports MARKET, LIMIT, and STOP orders only"
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

            created_at = self._now()
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
                strategy_lifecycle_id=order.strategy_lifecycle_id,
                structural_stop_price=_structural_stop_from_placement(order),
                execution_reason=_execution_reason_from_placement(order),
                entry_valid_until=_entry_valid_until_from_placement(
                    order,
                    created_at,
                ),
            )

            paper_order = create_order(
                paper_request,
                clock=lambda: created_at,
            )
            paper_order = accept_order(
                paper_order,
                at=paper_order.created_at,
            )
            if self._order_book.contains(paper_order.order_id):
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

            accepted_event = self._order_event(
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
            working_event = self._order_event(
                paper_order,
                event_type="ORDER_WORKING",
                message="Paper order is working.",
                status="WORKING",
            )
            self._persist_batch(
                (accepted_event, working_event),
                paper_order,
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
            self._emit_event(accepted_event)
            self._emit_event(working_event)
            # A DAY submitted outside its valid calendar/session boundary is
            # recorded truthfully, then terminalized before it can match.
            for event in self._expire_temporally_invalid_orders(created_at):
                self._emit_event(event)

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
                self._require_durability()
                terminal_reason = _cancellation_reason(request)
                cancelled = transition_cancel_order(
                    existing,
                    at=self._now(),
                    reason=terminal_reason,
                )
                event = self._order_event(
                    cancelled,
                    event_type="ORDER_CANCELLED",
                    message=(
                        f"Order {cancelled.order_id} cancelled: "
                        f"{terminal_reason.value}."
                    ),
                )
                self._persist_event(event, cancelled)
                self._order_book.update(cancelled)
                report = ExecutionReport(
                    order=cancelled,
                    match_result=None,
                    fills=(),
                    message=f"Order {cancelled.order_id} cancelled",
                )
                self._append_journal(
                    JournalEventType.CANCELLATION,
                    request.request_id,
                    report.order.updated_at,
                    report.message,
                    (("reason", terminal_reason.value),),
                )
                self._emit_event(event)
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

        evaluated_at = self._clock()
        if event.received_timestamp is not None:
            processing_age = Decimal(str(max(
                0,
                (evaluated_at - event.received_timestamp).total_seconds(),
            )))
            if processing_age > self._maximum_processing_age_seconds:
                performance_diagnostics.increment("processing_delayed_events")
                return ()
        source_age = Decimal(str(max(
            0,
            (evaluated_at - event.timestamp).total_seconds(),
        )))
        if source_age > self._maximum_processing_age_seconds:
            performance_diagnostics.increment("processing_delayed_events")
            return ()

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
            if self._durability_error is not None:
                return ()
            try:
                temporal_events = self._expire_temporally_invalid_orders(
                    evaluated_at,
                )
                invalidation_events = self._invalidate_entry_orders(quote)
            except PaperDurabilityError:
                return ()
            for event in temporal_events:
                self._emit_event(event)
            for event in invalidation_events:
                self._emit_event(event)
            durable_transitions: list[
                tuple[ExecutionReport, PaperRuntimeEvent]
            ] = []

            def persist_before_update(report: ExecutionReport) -> None:
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
                event = self._order_event(
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
                self._persist_event(event, report.order)
                durable_transitions.append((report, event))

            try:
                reports = self._execution_engine.process_quote(
                    quote,
                    before_update=persist_before_update,
                )
            except PaperDurabilityError:
                return ()

            for report, event in durable_transitions:
                fill = report.fills[0]
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
                self._emit_event(event)
            return reports

    def _invalidate_entry_orders(
        self,
        quote: MarketQuote,
    ) -> tuple[PaperRuntimeEvent, ...]:
        """Cancel risk-invalid LONG entries before matching the fresh quote."""

        events: list[PaperRuntimeEvent] = []
        for order in self._order_book.open_orders_for_symbol(quote.symbol):
            stop = _entry_structural_stop(order)
            if (
                order.side is not PaperOrderSide.BUY
                or stop is None
                or quote.ask_price > stop
            ):
                continue
            cancelled = transition_cancel_order(
                order,
                at=quote.timestamp,
                reason=OrderTerminalReason.STRUCTURAL_STOP_INVALIDATED,
            )
            event = self._order_event(
                cancelled,
                event_type="ORDER_CANCELLED",
                message=(
                    "Working entry invalidated before fill: executable ask "
                    f"{quote.ask_price} is at or below structural stop {stop}."
                ),
            )
            # The durable cancellation owns the transition.  A failed write
            # leaves the in-memory order unchanged and disables PAPER.
            self._persist_event(event, cancelled)
            self._order_book.update(cancelled)
            self._append_journal(
                JournalEventType.CANCELLATION,
                order.order_id,
                quote.timestamp,
                event.message,
                (("reason", "STRUCTURAL_STOP_INVALIDATED"),),
            )
            events.append(event)
        return tuple(events)

    def reconcile_temporal_validity(
        self,
        *,
        at: datetime | None = None,
    ) -> tuple[PaperRuntimeEvent, ...]:
        """Durably terminalize invalid restored/current entries before use."""

        evaluated_at = self._now() if at is None else at
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("validity reconciliation time must be timezone-aware")
        with self._lock:
            events = self._expire_temporally_invalid_orders(evaluated_at)
        for event in events:
            self._emit_event(event)
        return events

    def _expire_temporally_invalid_orders(
        self,
        evaluated_at: datetime,
    ) -> tuple[PaperRuntimeEvent, ...]:
        events: list[PaperRuntimeEvent] = []
        for order in self._order_book.open_orders():
            reason = temporal_terminal_reason(
                order,
                evaluated_at=evaluated_at,
                long_position_quantity=self._long_position_quantity(order.symbol),
            )
            if reason is None:
                continue
            expired = transition_expire_order(
                order,
                at=evaluated_at,
                reason=reason,
            )
            message = (
                f"Order {order.order_id} expired before matching: "
                f"{reason.value}."
            )
            event = self._order_event(
                expired,
                event_type="ORDER_EXPIRED",
                message=message,
            )
            self._persist_event(event, expired)
            self._order_book.update(expired)
            self._append_journal(
                JournalEventType.EXPIRATION,
                order.order_id,
                evaluated_at,
                message,
                (("reason", reason.value),),
            )
            events.append(event)
        return tuple(events)

    def _long_position_quantity(self, symbol: str) -> Decimal:
        quantity = Decimal("0")
        for order in self._order_book.history():
            if order.symbol != symbol or order.filled_quantity <= 0:
                continue
            if order.side is PaperOrderSide.BUY:
                quantity += order.filled_quantity
            else:
                quantity -= order.filled_quantity
        return max(quantity, Decimal("0"))

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
                    order_type=order.order_type.value,
                    limit_price=_decimal_text(order.limit_price),
                    stop_price=_decimal_text(order.stop_price),
                    filled_quantity="0",
                    remaining_quantity=format(order.quantity, "f"),
                    submitted_at=timestamp,
                    lifecycle_id=getattr(
                        order, "strategy_lifecycle_id", None
                    ),
                    execution_reason=_execution_reason_from_placement(order),
                    execution_source=self._source,
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
            self._order_event(
                order,
                event_type=event_type,
                message=message,
                status=status,
                fill=fill,
                mark_price=mark_price,
                decision=decision,
            ),
            order=order,
        )

    def _order_event(
        self,
        order: PaperOrder,
        *,
        event_type: str,
        message: str,
        status: str | None = None,
        fill: PaperFill | None = None,
        mark_price: Decimal | None = None,
        decision: RuntimeDecision | None = None,
    ) -> PaperRuntimeEvent:
        return PaperRuntimeEvent(
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
                order_type=order.request.order_type.value,
                limit_price=_decimal_text(order.request.limit_price),
                stop_price=_decimal_text(order.request.stop_price),
                filled_quantity=format(order.filled_quantity, "f"),
                remaining_quantity=format(order.remaining_quantity, "f"),
                average_fill_price=_decimal_text(order.average_fill_price),
                submitted_at=order.created_at,
                lifecycle_id=order.request.strategy_lifecycle_id,
                execution_reason=(
                    order.terminal_reason.value
                    if order.terminal_reason is not None
                    else order.request.execution_reason
                ),
                execution_source=self._source,
            ),
            fill=fill,
            mark_price=mark_price,
            decision=decision,
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

    def _publish(
        self,
        event: PaperRuntimeEvent,
        *,
        order: PaperOrder | None = None,
    ) -> None:
        if self._durable_store is not None:
            durable_order = order
            if durable_order is None and event.order is not None:
                try:
                    durable_order = self._order_book.get(event.order.order_id)
                except OrderNotFoundError:
                    durable_order = None
            self._persist_event(event, durable_order)
        self._emit_event(event)

    def _persist_event(
        self,
        event: PaperRuntimeEvent,
        order: PaperOrder | None,
    ) -> None:
        if self._durable_store is None:
            return
        self._require_durability()
        try:
            self._durable_store.persist(event, order)
        except Exception as exc:
            self._mark_durability_failed(exc, event.symbol)
            raise PaperDurabilityError(
                "authoritative PAPER persistence failed; PAPER is disabled"
            ) from exc

    def _persist_batch(
        self,
        events: tuple[PaperRuntimeEvent, ...],
        order: PaperOrder | None,
    ) -> None:
        if self._durable_store is None:
            return
        self._require_durability()
        try:
            self._durable_store.persist_batch(events, order)
        except Exception as exc:
            symbol = next((event.symbol for event in events if event.symbol), None)
            self._mark_durability_failed(exc, symbol)
            raise PaperDurabilityError(
                "authoritative PAPER persistence failed; PAPER is disabled"
            ) from exc

    def _mark_durability_failed(
        self,
        error: Exception,
        symbol: str | None,
    ) -> None:
        if self._durability_error is not None:
            return
        self._durability_error = error
        _LOGGER.critical(
            "PAPER durable persistence failed; execution is fail-closed",
            exc_info=(type(error), error, error.__traceback__),
        )
        if self._event_sink is None:
            return
        diagnostic = PaperRuntimeEvent(
            sequence=self._next_sequence(),
            timestamp=self._now(),
            event_type="PAPER_DURABILITY_FAILED",
            message="Authoritative PAPER persistence failed; PAPER execution is disabled.",
            cycle=0,
            symbol=symbol,
            source=self._source,
            health=RuntimeHealthUpdate(
                runtime_status="DEGRADED",
                risk_status="BLOCKED",
                persistence_status="FAILED",
                last_error=f"{type(error).__name__}: {error}",
            ),
        )
        try:
            self._event_sink(diagnostic)
        except Exception:
            _LOGGER.exception("PAPER durability diagnostic sink failed")

    def _require_durability(self) -> None:
        if self._durability_error is not None:
            raise PaperDurabilityError(
                "authoritative PAPER persistence is unavailable; PAPER is disabled"
            ) from self._durability_error

    def _emit_event(self, event: PaperRuntimeEvent) -> None:
        if self._event_sink is not None:
            self._event_sink(event)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("paper gateway clock must be timezone-aware")
        return value


def _structural_stop_from_placement(order: object) -> Decimal | None:
    """Read explicit strategy risk metadata without changing order semantics."""

    value = getattr(order, "metadata", {}).get("structural_stop")
    if value is None:
        return None
    try:
        stop = Decimal(str(value))
    except Exception:
        return None
    return stop if stop.is_finite() and stop > 0 else None


def _execution_reason_from_placement(order: object) -> str | None:
    value = getattr(order, "metadata", {}).get("reason")
    return None if value is None else str(value).strip().upper() or None


def _entry_valid_until_from_placement(
    order: object,
    created_at: datetime,
) -> datetime | None:
    """Persist the authorization-time validity supplied by Warrior PAPER."""

    metadata = getattr(order, "metadata", {})
    if metadata.get("source") != "autonomous-paper":
        return None
    value = metadata.get("entry_validity_seconds")
    try:
        seconds = Decimal(str(value))
    except Exception:
        return None
    if not seconds.is_finite() or seconds <= 0:
        return None
    return created_at + timedelta(seconds=float(seconds))


def _cancellation_reason(
    request: OrderCancellationRequest,
) -> OrderTerminalReason:
    source = str(request.metadata.get("source", "")).strip().lower()
    if source == "autonomous-paper-protective-replace":
        return OrderTerminalReason.PROTECTIVE_REPLACED
    return OrderTerminalReason.OPERATOR_CANCELLED


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _entry_structural_stop(order: PaperOrder) -> Decimal | None:
    """Resolve new explicit metadata, with a legacy Warrior lifecycle fallback."""

    if order.request.structural_stop_price is not None:
        return order.request.structural_stop_price
    lifecycle = order.request.strategy_lifecycle_id
    if not lifecycle or not lifecycle.startswith("WARRIOR_MOMENTUM_V1|"):
        return None
    try:
        stop = Decimal(lifecycle.rsplit("|", 1)[1])
    except Exception:
        return None
    return stop if stop.is_finite() and stop > 0 else None


__all__ = ["PaperDurabilityError", "PaperOrderGateway", "utc_now"]
