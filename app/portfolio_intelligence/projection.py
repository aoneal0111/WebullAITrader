"""Replayable projection over the existing ordered paper-runtime event stream."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal
from threading import RLock
from typing import Callable, Protocol

from app.operations.runtime import PaperRuntimeEvent
from app.read_models.orders.models import OrdersReadModelSnapshot
from app.read_models.positions.models import PositionsReadModelSnapshot

from .events import MeaningfulChangeDetector, PortfolioObservationEvent
from .models import (
    OrderSide, PortfolioAccount, PortfolioFill, PortfolioIntelligenceInput,
    PortfolioIntelligenceSnapshot, PortfolioPosition, PriceObservation,
    WorkingOrder,
)
from .runtime import PortfolioIntelligenceService


class PositionSource(Protocol):
    @property
    def snapshot(self) -> PositionsReadModelSnapshot: ...


class OrderSource(Protocol):
    @property
    def snapshot(self) -> OrdersReadModelSnapshot: ...


class PortfolioIntelligenceProjection:
    """Fold persisted runtime facts without broker reads or hidden GUI state."""

    def __init__(
        self,
        *,
        account_id: str,
        position_projection: PositionSource,
        order_projection: OrderSource,
        service: PortfolioIntelligenceService | None = None,
        account_source: Callable[[], PortfolioAccount] | None = None,
        observation_sink: Callable[[PortfolioObservationEvent], None] | None = None,
        snapshot_sink: Callable[[PortfolioIntelligenceSnapshot], None] | None = None,
    ) -> None:
        self._account_id = account_id
        self._positions = position_projection
        self._orders = order_projection
        self._service = service or PortfolioIntelligenceService()
        self._account_source = account_source
        self._observation_sink = observation_sink
        self._snapshot_sink = snapshot_sink
        self._detector = MeaningfulChangeDetector(
            concentration_warning=self._service.configuration.concentration_warning_threshold,
            concentration_critical=self._service.configuration.concentration_critical_threshold,
        )
        self._snapshot: PortfolioIntelligenceSnapshot | None = None
        self._fills: dict[str, PortfolioFill] = {}
        self._history_limit = self._service.configuration.correlation_lookback + 1
        self._history: dict[str, deque[PriceObservation]] = defaultdict(
            lambda: deque(maxlen=self._history_limit)
        )
        self._strategy_by_symbol: dict[str, tuple[str, str]] = {}
        self._last_sequence_by_source: dict[str, int] = {}
        self._lock = RLock()

    @property
    def snapshot(self) -> PortfolioIntelligenceSnapshot | None:
        with self._lock:
            return self._snapshot

    def __call__(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be PaperRuntimeEvent")
        with self._lock:
            if event.sequence <= self._last_sequence_by_source.get(event.source, 0):
                return
            self._last_sequence_by_source[event.source] = event.sequence
            relevant_symbols = self._relevant_symbols()
            if event.decision is not None:
                self._strategy_by_symbol[event.decision.symbol] = (event.decision.strategy_id, event.decision.action)
            if event.fill is not None:
                strategy, decision = self._strategy_by_symbol.get(event.fill.symbol, ("Unattributed", "Unattributed"))
                self._fills[event.fill.request_id] = PortfolioFill(
                    event.fill.request_id, event.fill.symbol, OrderSide(event.fill.side),
                    event.fill.quantity, event.fill.fill_price, event.fill.timestamp,
                    event.fill.realized_pnl, "EQUITY", strategy, decision,
                )
            if (
                event.mark_price is not None
                and event.symbol is not None
                and event.symbol in relevant_symbols
            ):
                self._history[event.symbol].append(PriceObservation(event.timestamp, event.mark_price))
            current_relevant_symbols = self._relevant_symbols()
            for symbol in tuple(self._history):
                if symbol not in current_relevant_symbols:
                    del self._history[symbol]
            affects_state = (
                self._snapshot is None
                or event.fill is not None
                or event.order is not None
                or (
                    event.decision is not None
                    and event.decision.symbol in current_relevant_symbols
                )
                or (
                    event.mark_price is not None
                    and event.symbol in current_relevant_symbols
                )
            )
            if not affects_state:
                return
            source = self._input(event.timestamp)
            previous = self._snapshot
            current = self._service.build(source)
            if current == previous:
                return
            self._snapshot = current
            changes = self._detector.detect(previous, current)
        if self._observation_sink is not None:
            for change in changes:
                self._observation_sink(change)
        if self._snapshot_sink is not None:
            self._snapshot_sink(current)

    def _relevant_symbols(self) -> set[str]:
        symbols = {
            item.symbol
            for item in self._positions.snapshot.positions
            if Decimal(item.quantity) != 0
        }
        symbols.update(
            item.symbol
            for item in self._orders.snapshot.orders
            if item.status.upper().replace(" ", "_") in _WORKING_ORDER_STATUSES
        )
        return symbols

    def _input(self, timestamp: datetime) -> PortfolioIntelligenceInput:
        account = self._account_source() if self._account_source is not None else PortfolioAccount(self._account_id, None, None, None)
        positions = tuple(_position(item, self._strategy_by_symbol.get(item.symbol)) for item in self._positions.snapshot.positions if Decimal(item.quantity) != 0)
        orders = tuple(_order(item) for item in self._orders.snapshot.orders if item.status.upper().replace(" ", "_") in _WORKING_ORDER_STATUSES)
        return PortfolioIntelligenceInput(
            account, positions, orders,
            tuple(sorted(self._fills.values(), key=lambda fill: (fill.timestamp, fill.fill_id))),
            {symbol: tuple(points) for symbol, points in self._history.items()},
            (), timestamp,
        )


def _position(item, attribution) -> PortfolioPosition:
    quantity = Decimal(item.quantity)
    mark = abs(Decimal(item.market_value) / quantity) if item.market_value is not None and quantity != 0 else None
    strategy, decision = attribution or (None, None)
    return PortfolioPosition(item.symbol, quantity, Decimal(item.average_cost), mark, item.asset_type, item.currency, strategy, decision)


def _order(item) -> WorkingOrder:
    return WorkingOrder(item.order_id, item.symbol, OrderSide(item.side.upper()), Decimal(item.quantity), None)


_WORKING_ORDER_STATUSES = frozenset({
    "ACCEPTED", "ACKNOWLEDGED", "NEW", "OPEN", "PARTIAL_FILL",
    "PARTIALLY_FILLED", "PENDING", "SUBMITTED", "WORKING",
})


__all__ = ["PortfolioIntelligenceProjection"]
