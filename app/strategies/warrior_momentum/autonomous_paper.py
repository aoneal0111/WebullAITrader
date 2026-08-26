"""PAPER-only execution bridge with trade-lifecycle idempotency."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from threading import RLock
from typing import Callable
from enum import StrEnum

from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import OrderSide
from app.services.order_command_factory import OrderCommandFactory, OrderEntryCommand
from app.services.trading_service import TradingService


_TERMINAL_EXIT_REASONS = frozenset({"STOP", "STOP_LOSS", "EXIT", "RUNNER_TARGET"})
_RECENT_LIFECYCLE_LIMIT = 1024


class AutonomousPaperReadiness(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RECONCILING = "RECONCILING"
    READY = "READY"
    BLOCKED = "BLOCKED"


class AutonomousManagementReadiness(StrEnum):
    READY = "READY"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


def lifecycle_identity(signal: object) -> str:
    """Derive a stable identity from the existing Warrior signal boundary."""
    explicit = getattr(signal, "lifecycle_id", None)
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    values = (
        getattr(signal, "strategy_id", "warrior_momentum"),
        str(getattr(signal, "symbol", "")).strip().upper(),
        getattr(signal, "timestamp", None),
        getattr(signal, "setup_type", ""),
        getattr(signal, "entry_trigger", ""),
        getattr(signal, "stop_price", ""),
    )
    return "|".join(str(value) for value in values)


@dataclass(slots=True)
class AutonomousPaperExecutionBridge:
    """Convert approved Warrior signals into the real PAPER order boundary.

    Forward-capture records remain analytical. The paper order book and its
    gateway events are authoritative for accepted orders, fills, positions,
    and P&L. Lifecycle identity is retained until its terminal exit is
    reconciled, allowing a later setup for the same symbol to trade again.
    """

    trading_service: TradingService
    order_command_factory: OrderCommandFactory
    mode: str = "PAPER"
    enabled: bool = True
    order_book: PaperOrderBook | None = None
    position_quantity_source: Callable[[str], Decimal] | None = None
    management_context_source: Callable[[str], str | None] | None = None
    _seen_entries: OrderedDict[str, None] = field(default_factory=OrderedDict, init=False)
    _active_by_symbol: dict[str, str] = field(default_factory=dict, init=False)
    _entry_orders: dict[str, str] = field(default_factory=dict, init=False)
    _exit_orders: dict[tuple[str, str], str] = field(default_factory=dict, init=False)
    _exit_keys: OrderedDict[tuple[str, str], None] = field(default_factory=OrderedDict, init=False)
    _readiness: AutonomousPaperReadiness = field(default=AutonomousPaperReadiness.READY, init=False)
    _management_incomplete: set[str] = field(default_factory=set, init=False)
    _recovered_symbols: set[str] = field(default_factory=set, init=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.mode.strip().upper() != "PAPER":
            self.enabled = False

    @property
    def readiness(self) -> AutonomousPaperReadiness:
        return self._readiness

    def management_readiness(self, symbol: str) -> AutonomousManagementReadiness:
        normalized = symbol.strip().upper()
        active_identity = self._active_by_symbol.get(normalized)
        if (
            normalized in self._recovered_symbols
            and self._authoritative_quantity(normalized) > 0
            and self.management_context_source is None
        ):
            return AutonomousManagementReadiness.RECONCILIATION_REQUIRED
        if (
            self._authoritative_quantity(normalized) > 0
            and active_identity
            and self.management_context_source is not None
            and self.management_context_source(normalized) != active_identity
        ):
            return AutonomousManagementReadiness.RECONCILIATION_REQUIRED
        if (
            (active_identity or "").startswith("recovered:")
            and self._authoritative_quantity(normalized) > 0
            and not (
                self.management_context_source is not None
                and self.management_context_source(normalized) is not None
            )
        ):
            return AutonomousManagementReadiness.RECONCILIATION_REQUIRED
        return (
            AutonomousManagementReadiness.RECONCILIATION_REQUIRED
            if normalized in self._management_incomplete
            else AutonomousManagementReadiness.READY
        )

    def begin_reconciliation(self) -> None:
        """Close the entry gate while authoritative PAPER state is inspected."""
        with self._lock:
            self._readiness = AutonomousPaperReadiness.RECONCILING

    def reconcile(self) -> AutonomousPaperReadiness:
        """Rebuild conservative ownership from restored execution state.

        The order book is authoritative here; analytical Warrior capture state
        is intentionally not consulted.  A contradiction fails closed.
        """
        with self._lock:
            self._readiness = AutonomousPaperReadiness.RECONCILING
            if self.order_book is None:
                self._readiness = AutonomousPaperReadiness.READY
                return self._readiness
            self._management_incomplete.clear()
            working = self.order_book.open_orders()
            by_symbol: dict[str, list[object]] = {}
            for order in working:
                by_symbol.setdefault(order.symbol, []).append(order)
            for order in self.order_book.history():
                if self._authoritative_quantity(order.symbol) > 0:
                    by_symbol.setdefault(order.symbol, [])
            for symbol, orders in by_symbol.items():
                buys = [item for item in orders if item.request.side is OrderSide.BUY]
                sells = [item for item in orders if item.request.side is OrderSide.SELL]
                if len(buys) > 1 or (sells and self._authoritative_quantity(symbol) <= 0):
                    self._readiness = AutonomousPaperReadiness.BLOCKED
                    return self._readiness
                if buys or sells or self._authoritative_quantity(symbol) > 0:
                    recovered_identity, ambiguous = self._execution_lifecycle(symbol)
                    # A not-yet-filled working entry has no contribution in
                    # the net position reducer, but its opaque correlation
                    # metadata is still authoritative ownership.
                    if recovered_identity is None and len(buys) == 1:
                        recovered_identity = buys[0].request.strategy_lifecycle_id
                    if (
                        recovered_identity is not None
                        and buys
                        and any(
                            item.request.strategy_lifecycle_id not in (None, recovered_identity)
                            for item in buys
                        )
                    ):
                        self._readiness = AutonomousPaperReadiness.BLOCKED
                        return self._readiness
                    if ambiguous:
                        self._readiness = AutonomousPaperReadiness.BLOCKED
                        return self._readiness
                    context_identity = (
                        None if self.management_context_source is None
                        else self.management_context_source(symbol)
                    )
                    if recovered_identity is not None and context_identity not in (None, recovered_identity):
                        self._readiness = AutonomousPaperReadiness.BLOCKED
                        return self._readiness
                    recovered_identity = recovered_identity or context_identity
                    self._active_by_symbol[symbol] = recovered_identity or f"recovered:{symbol}"
                    self._recovered_symbols.add(symbol)
                    if self._authoritative_quantity(symbol) > 0 and (
                        recovered_identity is None or context_identity != recovered_identity
                    ):
                        self._management_incomplete.add(symbol)
                    if sells:
                        sell_identity = sells[0].request.strategy_lifecycle_id
                        if recovered_identity and sell_identity and sell_identity != recovered_identity:
                            self._readiness = AutonomousPaperReadiness.BLOCKED
                            return self._readiness
                        recovered_key = (self._active_by_symbol[symbol], "STOP")
                        self._exit_orders[recovered_key] = sells[0].order_id
                        self._remember(self._exit_keys, recovered_key)
            self._readiness = AutonomousPaperReadiness.READY
            for symbol, identity in tuple(self._active_by_symbol.items()):
                if identity.startswith("recovered:") and self._authoritative_quantity(symbol) <= 0 and not self.order_book.open_orders_for_symbol(symbol):
                    self._active_by_symbol.pop(symbol, None)
                    self._management_incomplete.discard(symbol)
                    self._recovered_symbols.discard(symbol)
        return self._readiness

    def _execution_lifecycle(self, symbol: str) -> tuple[str | None, bool]:
        """Derive the lifecycle contributing the current net position.

        Closed historical lifecycles net to zero and therefore cannot shadow
        a later same-symbol trade.  More than one positive contributor, or an
        unidentified contributor mixed with an identified one, is ambiguous.
        """
        contributions: dict[str | None, Decimal] = {}
        if self.order_book is None:
            return None, False
        for order in self.order_book.history():
            if order.symbol != symbol or order.filled_quantity <= 0:
                continue
            identity = order.request.strategy_lifecycle_id
            delta = order.filled_quantity if order.request.side is OrderSide.BUY else -order.filled_quantity
            contributions[identity] = contributions.get(identity, Decimal("0")) + delta
        active = {identity: value for identity, value in contributions.items() if value > 0}
        if len(active) == 1:
            identity = next(iter(active))
            return identity, identity is None and len(active) > 1
        return None, len(active) > 1

    def submit_entry(self, signal: object, shares: int, risk_dollars: Decimal) -> bool:
        symbol = str(getattr(signal, "symbol", "")).strip().upper()
        trigger = Decimal(getattr(signal, "entry_trigger"))
        identity = lifecycle_identity(signal)
        if not self._authorized(symbol) or shares <= 0 or self.readiness is not AutonomousPaperReadiness.READY:
            return False
        with self._lock:
            self._reconcile_terminal_exits()
            if self.order_book is not None and (
                self.order_book.open_orders_for_symbol(symbol)
                or self._authoritative_quantity(symbol) > 0
            ):
                return False
            if self._active_by_symbol.get(symbol) is not None:
                return False
            if self._identity_seen(identity):
                return False
            result = self.trading_service.place_order(
                self.order_command_factory.create_placement_request(
                    OrderEntryCommand(
                        symbol=symbol, side="BUY", quantity=Decimal(shares),
                        order_type="LIMIT", limit_price=trigger,
                        stop_price=None, time_in_force="DAY",
                        strategy_lifecycle_id=identity,
                        metadata={
                            "source": "autonomous-paper",
                            "risk_dollars": str(risk_dollars),
                            "lifecycle_id": identity,
                        },
                    )
                )
            )
            if not result.success:
                return False
            self._remember(self._seen_entries, identity)
            self._active_by_symbol[symbol] = identity
            self._entry_orders[identity] = result.broker_order_id
            return True

    def submit_exit(
        self, symbol: str, quantity: int, price: Decimal, reason: str,
        lifecycle_id: str | None = None,
    ) -> bool:
        normalized = symbol.strip().upper()
        reason_key = reason.strip().upper()
        if not self._authorized(normalized) or quantity <= 0 or self.readiness is not AutonomousPaperReadiness.READY:
            return False
        if (
            self.position_quantity_source is not None
            and Decimal(self.position_quantity_source(normalized)) < Decimal(quantity)
        ):
            return False
        with self._lock:
            self._reconcile_terminal_exits()
            if self._active_by_symbol.get(normalized, "").startswith("recovered:") and self._authoritative_quantity(normalized) > 0:
                self._management_incomplete.add(normalized)
            if self.management_readiness(normalized) is AutonomousManagementReadiness.RECONCILIATION_REQUIRED:
                return False
            if self.order_book is not None and any(
                order.request.side is OrderSide.SELL
                for order in self.order_book.open_orders_for_symbol(normalized)
            ):
                return False
            active = self._active_by_symbol.get(normalized)
            identity = lifecycle_id or active
            if identity is None or active != identity:
                return False
            key = (identity, reason_key)
            if key in self._exit_keys:
                return False
            result = self.trading_service.place_order(
                self.order_command_factory.create_placement_request(
                    OrderEntryCommand(
                        symbol=normalized, side="SELL", quantity=Decimal(quantity),
                        order_type="LIMIT", limit_price=Decimal(price),
                        stop_price=None, time_in_force="DAY",
                        strategy_lifecycle_id=identity,
                        metadata={
                            "source": "autonomous-paper",
                            "reason": reason_key,
                            "lifecycle_id": identity,
                        },
                    )
                )
            )
            if not result.success:
                return False
            self._remember(self._exit_keys, key)
            self._exit_orders[key] = result.broker_order_id
            return True

    def _reconcile_terminal_exits(self) -> None:
        if self.order_book is None:
            return
        terminal_ids = {order.order_id for order in self.order_book.terminal_orders()}
        for (identity, reason), order_id in tuple(self._exit_orders.items()):
            if order_id not in terminal_ids or reason not in _TERMINAL_EXIT_REASONS:
                continue
            symbol = next(
                (symbol for symbol, value in self._active_by_symbol.items() if value == identity),
                None,
            )
            if symbol is not None:
                self._active_by_symbol.pop(symbol, None)
                self._management_incomplete.discard(symbol)

    def _identity_seen(self, identity: str) -> bool:
        return identity in self._seen_entries

    def _authoritative_quantity(self, symbol: str) -> Decimal:
        quantity = Decimal("0")
        if self.order_book is None:
            return quantity
        for order in self.order_book.history():
            if order.symbol != symbol:
                continue
            if order.request.side is OrderSide.BUY:
                quantity += order.filled_quantity
            else:
                quantity -= order.filled_quantity
        return max(quantity, Decimal("0"))

    @staticmethod
    def _remember(store: OrderedDict, key: object) -> None:
        store[key] = None
        store.move_to_end(key)
        while len(store) > _RECENT_LIFECYCLE_LIMIT:
            store.popitem(last=False)

    def _authorized(self, symbol: str) -> bool:
        return bool(self.enabled and self.mode.strip().upper() == "PAPER" and symbol)


__all__ = ["AutonomousPaperExecutionBridge", "AutonomousManagementReadiness", "AutonomousPaperReadiness", "lifecycle_identity"]
