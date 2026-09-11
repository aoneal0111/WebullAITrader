"""PAPER-only execution bridge with trade-lifecycle idempotency."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from threading import RLock
from typing import Callable
from enum import StrEnum

from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import OrderSide, OrderType
from app.order_placement import OrderPlacementDecision
from app.services.order_command_factory import OrderCommandFactory, OrderEntryCommand
from app.services.trading_service import TradingService
from app.strategies.warrior_momentum.features import BAR_INTERVAL
from app.performance_diagnostics import performance_diagnostics


_RECENT_LIFECYCLE_LIMIT = 1024


class AutonomousPaperReadiness(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RECONCILING = "RECONCILING"
    READY = "READY"
    BLOCKED = "BLOCKED"


class AutonomousManagementReadiness(StrEnum):
    READY = "READY"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class PaperEntryAuthorizationResult(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    REFUSED = "REFUSED"
    DEFERRED = "DEFERRED"


class PaperEntryAuthorizationReason(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    PAPER_DISABLED = "PAPER_DISABLED"
    ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    BROKER_NOT_READY = "BROKER_NOT_READY"
    WORKING_ORDER_EXISTS = "WORKING_ORDER_EXISTS"
    POSITION_EXISTS = "POSITION_EXISTS"
    DUPLICATE_LIFECYCLE = "DUPLICATE_LIFECYCLE"
    ORDER_CONSTRUCTION_FAILED = "ORDER_CONSTRUCTION_FAILED"
    ORDER_PLACEMENT_DISABLED = "ORDER_PLACEMENT_DISABLED"
    SESSION_BLOCKED = "SESSION_BLOCKED"
    GATEWAY_FAILURE = "GATEWAY_FAILURE"
    ORDER_REJECTED = "ORDER_REJECTED"
    EXECUTION_DATA_UNAVAILABLE = "EXECUTION_DATA_UNAVAILABLE"
    EXECUTION_NOT_PERMITTED = "EXECUTION_NOT_PERMITTED"
    ACCOUNT_NOT_READY = "ACCOUNT_NOT_READY"
    SYMBOL_NOT_ALLOWED = "SYMBOL_NOT_ALLOWED"
    SPREAD_WIDE = "SPREAD_WIDE"
    HALT_UNKNOWN = "HALT_UNKNOWN"
    RISK_REJECTED = "RISK_REJECTED"
    BROKER_RESTRICTED = "BROKER_RESTRICTED"
    BUYING_POWER_INSUFFICIENT = "BUYING_POWER_INSUFFICIENT"
    EXPOSURE_LIMIT = "EXPOSURE_LIMIT"
    CHASE_WINDOW_EXPIRED = "CHASE_WINDOW_EXPIRED"
    OPPORTUNITY_LIFECYCLE_LIMIT_EXHAUSTED = "OPPORTUNITY_LIFECYCLE_LIMIT_EXHAUSTED"
    PARTIAL_POSITION_EXISTS = "PARTIAL_POSITION_EXISTS"


class PaperExitSubmissionState(StrEnum):
    SUBMITTED = "SUBMITTED"
    WORKING = "WORKING"
    UNAVAILABLE = "UNAVAILABLE"


class PaperEntryReplacementState(StrEnum):
    SUBMITTED = "SUBMITTED"
    REFUSED = "REFUSED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class PaperEntryReplacementDecision:
    state: PaperEntryReplacementState
    symbol: str
    lifecycle_id: str
    reason: str
    predecessor_order_id: str | None = None
    replacement_order_id: str | None = None
    replacement_sequence: int = 0
    requested_quantity: int = 0
    cumulative_filled_quantity: Decimal = Decimal("0")

    @property
    def submitted(self) -> bool:
        return self.state is PaperEntryReplacementState.SUBMITTED


@dataclass(frozen=True, slots=True)
class PaperEntryReplacementPolicy:
    max_replacements: int = 2
    min_reprice_interval_seconds: Decimal = Decimal("5.0")
    max_displacement_percent: Decimal = Decimal("1.5")
    max_displacement_absolute: Decimal = Decimal("0.05")

    def __post_init__(self) -> None:
        if self.max_replacements < 0:
            raise ValueError("replacement count cannot be negative")
        if self.min_reprice_interval_seconds < 0:
            raise ValueError("reprice interval cannot be negative")
        if self.max_displacement_percent <= 0 or self.max_displacement_absolute <= 0:
            raise ValueError("displacement caps must be positive")


@dataclass(frozen=True, slots=True)
class PaperExitSubmissionDecision:
    state: PaperExitSubmissionState
    symbol: str
    lifecycle_id: str | None
    reason: str
    order_id: str | None = None
    activation_timestamp: datetime | None = None

    @property
    def protection_active(self) -> bool:
        return self.state in {
            PaperExitSubmissionState.SUBMITTED,
            PaperExitSubmissionState.WORKING,
        }


@dataclass(frozen=True, slots=True)
class PaperEntryGateDecision:
    gate: str
    passed: bool
    observed: str
    required: str


@dataclass(frozen=True, slots=True)
class PaperEntryAuthorizationDecision:
    """Passive account of one attempt at the existing PAPER entry boundary."""

    result: PaperEntryAuthorizationResult
    reason: PaperEntryAuthorizationReason
    symbol: str
    lifecycle_id: str
    gates: tuple[PaperEntryGateDecision, ...]
    order_constructed: bool = False
    submission_attempted: bool = False
    placement_decision: str | None = None

    @property
    def authorized(self) -> bool:
        return self.result is PaperEntryAuthorizationResult.AUTHORIZED


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


def opportunity_identity(signal: object) -> str:
    """Return the stable anchor for one strategy-qualified opportunity."""
    explicit = getattr(signal, "opportunity_id", None)
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    values = (
        getattr(signal, "strategy_id", "warrior_momentum"),
        str(getattr(signal, "symbol", "")).strip().upper(),
        getattr(getattr(signal, "setup_type", None), "value", getattr(signal, "setup_type", "")),
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
                if order.request.strategy_lifecycle_id is not None:
                    self._remember(
                        self._seen_entries,
                        order.request.strategy_lifecycle_id,
                    )
                if self._authoritative_quantity(order.symbol) > 0:
                    by_symbol.setdefault(order.symbol, [])
            for symbol, orders in by_symbol.items():
                buys = [item for item in orders if item.request.side is OrderSide.BUY]
                sells = [item for item in orders if item.request.side is OrderSide.SELL]
                stops = [item for item in sells if item.request.order_type is OrderType.STOP]
                if len(stops) > 1:
                    identities = {item.request.strategy_lifecycle_id for item in stops}
                    if len(identities) != 1:
                        self._readiness = AutonomousPaperReadiness.BLOCKED
                        return self._readiness
                    keeper = max(stops, key=lambda item: (item.updated_at, item.order_id))
                    if any(
                        not self._cancel_working_order(item)
                        for item in stops if item.order_id != keeper.order_id
                    ):
                        self._readiness = AutonomousPaperReadiness.BLOCKED
                        return self._readiness
                    sells = [
                        item for item in self.order_book.open_orders_for_symbol(symbol)
                        if item.request.side is OrderSide.SELL
                    ]
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
                    if buys and recovered_identity is not None:
                        self._entry_orders[recovered_identity] = buys[0].order_id
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
                        recovered_reason = (
                            sells[0].request.execution_reason
                            or "LEGACY_EXIT_UNKNOWN"
                        )
                        recovered_key = (
                            self._active_by_symbol[symbol], recovered_reason,
                        )
                        self._exit_orders[recovered_key] = sells[0].order_id
                        self._remember(self._exit_keys, recovered_key)
                        if (
                            recovered_reason in {
                                "STOP", "STOP_LOSS", "LEGACY_EXIT_UNKNOWN",
                            }
                            and sells[0].request.order_type is not OrderType.STOP
                        ):
                            # Legacy STOP requests were persisted as LIMITs.
                            # Retain ownership and suppress duplicates, but do
                            # not describe that unmarketable order as healthy
                            # protective management.
                            self._management_incomplete.add(symbol)
            self._readiness = AutonomousPaperReadiness.READY
            for symbol, identity in tuple(self._active_by_symbol.items()):
                if identity.startswith("recovered:") and self._authoritative_quantity(symbol) <= 0 and not self.order_book.open_orders_for_symbol(symbol):
                    self._active_by_symbol.pop(symbol, None)
                    self._management_incomplete.discard(symbol)
                    self._recovered_symbols.discard(symbol)
        return self._readiness

    def reconcile_protection(self) -> tuple[str, ...]:
        """Ensure every restored authoritative long has one correlated stop."""

        reconciled: list[str] = []
        with self._lock:
            if self.order_book is None or self.readiness is not AutonomousPaperReadiness.READY:
                return ()
            for symbol, identity in tuple(self._active_by_symbol.items()):
                quantity = int(self._authoritative_quantity(symbol))
                if quantity <= 0 or identity is None:
                    continue
                stop = self._structural_stop(symbol, identity)
                if stop is None:
                    self._management_incomplete.add(symbol)
                    continue
                if not self._reconcile_correlated_exits(symbol, identity):
                    self._management_incomplete.add(symbol)
                    continue
                stops = tuple(
                    order for order in self.order_book.open_orders_for_symbol(symbol)
                    if order.request.side is OrderSide.SELL
                    and order.request.strategy_lifecycle_id == identity
                    and order.request.order_type is OrderType.STOP
                )
                if stops and int(stops[0].remaining_quantity) == quantity:
                    self._management_incomplete.discard(symbol)
                    performance_diagnostics.record_protection_event(
                        state="PROTECTION_RECONCILED",
                        symbol=symbol,
                        lifecycle_id=identity,
                        authoritative_open_quantity=quantity,
                        protected_quantity=quantity,
                        stop_price=stops[0].request.stop_price,
                        order_id=stops[0].order_id,
                        reason="RESTORED_PROTECTION_MATCHES_POSITION",
                    )
                    reconciled.append(symbol)
                    continue
                if stops and not self._cancel_working_order(stops[0]):
                    self._management_incomplete.add(symbol)
                    continue
                if stops:
                    self._exit_orders.pop((identity, "STOP"), None)
                    self._exit_keys.pop((identity, "STOP"), None)
                    self._reconcile_terminal_exits()
                result = self._place_exit(symbol, quantity, stop, "STOP", identity)
                if result.protection_active:
                    self._management_incomplete.discard(symbol)
                    performance_diagnostics.record_protection_event(
                        state="PROTECTION_RECONCILED",
                        symbol=symbol,
                        lifecycle_id=identity,
                        authoritative_open_quantity=quantity,
                        protected_quantity=quantity,
                        stop_price=stop,
                        order_id=result.order_id,
                        reason="RESTORED_PROTECTION_SUBMITTED",
                    )
                    reconciled.append(symbol)
                else:
                    self._management_incomplete.add(symbol)
        return tuple(sorted(set(reconciled)))

    def _structural_stop(self, symbol: str, identity: str) -> Decimal | None:
        if self.order_book is None:
            return None
        for order in reversed(self.order_book.history()):
            if (
                order.symbol == symbol
                and order.request.side is OrderSide.BUY
                and order.request.strategy_lifecycle_id == identity
            ):
                if order.request.structural_stop_price is not None:
                    return order.request.structural_stop_price
                raw_stop = order.request.metadata.get("structural_stop")
                if raw_stop is None:
                    return None
                try:
                    stop = Decimal(str(raw_stop))
                except (TypeError, ValueError):
                    return None
                return stop if stop > Decimal("0") else None
        return None

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

    def submit_entry_decision(
        self, signal: object, shares: int, risk_dollars: Decimal,
        *, opportunity_id: str | None = None,
        opportunity_anchor: Decimal | None = None,
        opportunity_deadline: datetime | None = None,
        lifecycle_number: int = 1,
        max_lifecycles_per_opportunity: int = 3,
        max_displacement_percent: Decimal = Decimal("1.5"),
        max_displacement_absolute: Decimal = Decimal("0.05"),
        provenance: str = "AUTONOMOUS_ORIGINAL_ENTRY",
    ) -> PaperEntryAuthorizationDecision:
        """Run the unchanged PAPER entry path and expose its terminal gate."""

        symbol = str(getattr(signal, "symbol", "")).strip().upper()
        trigger = Decimal(getattr(signal, "entry_trigger"))
        identity = lifecycle_identity(signal)
        opportunity = opportunity_id or opportunity_identity(signal)
        gates: list[PaperEntryGateDecision] = []
        performance_diagnostics.record_entry_counter("entry_authorizations")
        performance_diagnostics.record_entry_lifecycle(
            identity,
            symbol=symbol,
            setup_type=getattr(getattr(signal, "setup_type", None), "value", getattr(signal, "setup_type", None)),
            trigger_price=trigger,
            structural_stop=getattr(signal, "stop_price", None),
            authorization_timestamp=datetime.now(UTC),
            requested_quantity=shares,
            initial_limit=trigger,
        )

        def gate(name: str, passed: bool, observed: object, required: object) -> bool:
            gates.append(PaperEntryGateDecision(
                name, passed, str(observed), str(required),
            ))
            return passed

        def refused(
            reason: PaperEntryAuthorizationReason, *, constructed: bool = False,
            attempted: bool = False, placement: str | None = None,
        ) -> PaperEntryAuthorizationDecision:
            if reason is PaperEntryAuthorizationReason.DUPLICATE_LIFECYCLE:
                performance_diagnostics.record_entry_counter(
                    "same_lifecycle_suppression_after_expiry"
                )
            performance_diagnostics.record_entry_lifecycle(
                identity,
                risk_approved=False,
                terminal_state="AUTHORIZATION_REFUSED",
                expiry_reason=reason.value,
            )
            return PaperEntryAuthorizationDecision(
                PaperEntryAuthorizationResult.REFUSED, reason, symbol, identity,
                tuple(gates), constructed, attempted, placement,
            )

        if not gate("environment", self.mode.strip().upper() == "PAPER", self.mode, "PAPER"):
            return refused(PaperEntryAuthorizationReason.ENVIRONMENT_MISMATCH)
        if not gate("paper_enabled", self.enabled, self.enabled, True):
            return refused(PaperEntryAuthorizationReason.PAPER_DISABLED)
        if not gate("symbol", bool(symbol), bool(symbol), True):
            return refused(PaperEntryAuthorizationReason.INVALID_SYMBOL)
        if not gate("quantity", shares > 0, shares, "> 0"):
            return refused(PaperEntryAuthorizationReason.INVALID_QUANTITY)
        if not gate(
            "broker_readiness", self.readiness is AutonomousPaperReadiness.READY,
            self.readiness.value, AutonomousPaperReadiness.READY.value,
        ):
            return refused(PaperEntryAuthorizationReason.BROKER_NOT_READY)
        with self._lock:
            self._reconcile_terminal_entries()
            self._reconcile_terminal_exits()
            working = bool(
                self.order_book is not None
                and self.order_book.open_orders_for_symbol(symbol)
            )
            if not gate("working_order_clear", not working, working, False):
                return refused(PaperEntryAuthorizationReason.WORKING_ORDER_EXISTS)
            positioned = self._authoritative_quantity(symbol) > 0
            active = self._active_by_symbol.get(symbol) is not None
            if not gate("position_clear", not (positioned or active), positioned or active, False):
                return refused(PaperEntryAuthorizationReason.POSITION_EXISTS)
            duplicate = self._identity_seen(identity)
            if not gate("lifecycle_clear", not duplicate, duplicate, False):
                return refused(PaperEntryAuthorizationReason.DUPLICATE_LIFECYCLE)
            historical_lifecycles = set()
            if self.order_book is not None:
                historical_lifecycles = {
                    str(item.request.strategy_lifecycle_id)
                    for item in self.order_book.history()
                    if item.request.strategy_lifecycle_id
                    and item.request.metadata.get("opportunity_id") == opportunity
                    and item.request.side is OrderSide.BUY
                }
            if len(historical_lifecycles) >= max_lifecycles_per_opportunity:
                return refused(PaperEntryAuthorizationReason.DUPLICATE_LIFECYCLE)
            anchor = opportunity_anchor
            deadline = opportunity_deadline
            if self.order_book is not None:
                for item in self.order_book.history():
                    if item.request.metadata.get("opportunity_id") == opportunity:
                        prior = item.request.metadata
                        if anchor is None and prior.get("opportunity_original_entry") is not None:
                            anchor = Decimal(str(prior["opportunity_original_entry"]))
                        if deadline is None and prior.get("opportunity_deadline") is not None:
                            deadline = datetime.fromisoformat(str(prior["opportunity_deadline"]))
                        break
            anchor = trigger if anchor is None else anchor
            if deadline is None:
                signal_timestamp = getattr(signal, "timestamp", None)
                if isinstance(signal_timestamp, datetime):
                    deadline = signal_timestamp + BAR_INTERVAL
            if trigger > min(
                anchor * (Decimal("1") + max_displacement_percent / Decimal("100")),
                anchor + max_displacement_absolute,
            ):
                return refused(PaperEntryAuthorizationReason.EXPOSURE_LIMIT)
            try:
                request = self.order_command_factory.create_placement_request(
                    OrderEntryCommand(
                        symbol=symbol, side="BUY", quantity=Decimal(shares),
                        order_type="LIMIT", limit_price=trigger,
                        stop_price=None, time_in_force="DAY",
                        strategy_lifecycle_id=identity,
                        metadata={
                            "source": "autonomous-paper",
                            "reason": "ENTRY",
                            "provenance": provenance,
                            "opportunity_id": opportunity,
                            "opportunity_original_entry": str(anchor),
                            "opportunity_deadline": None if deadline is None else deadline.isoformat(),
                            "lifecycle_number": str(lifecycle_number),
                            "max_lifecycles_per_opportunity": str(max_lifecycles_per_opportunity),
                            "risk_dollars": str(risk_dollars),
                            "lifecycle_id": identity,
                            "replacement_sequence": "0",
                            "original_planned_entry": str(trigger),
                            "structural_stop": str(getattr(signal, "stop_price", "")),
                            "entry_validity_seconds": str(
                                int(BAR_INTERVAL.total_seconds())
                            ),
                        },
                    )
                )
            except Exception:
                gate("order_constructible", False, False, True)
                return refused(PaperEntryAuthorizationReason.ORDER_CONSTRUCTION_FAILED)
            gate("order_constructible", True, True, True)
            try:
                result = self.trading_service.place_order(request)
            except Exception:
                gate("submission", False, "EXCEPTION", "SUCCESS")
                return refused(
                    PaperEntryAuthorizationReason.GATEWAY_FAILURE,
                    constructed=True, attempted=True,
                )
            gate("submission", result.success, result.decision.value, "SUCCESS")
            if not result.success:
                reasons = {
                    OrderPlacementDecision.DISABLED: PaperEntryAuthorizationReason.ORDER_PLACEMENT_DISABLED,
                    OrderPlacementDecision.SESSION_INVALID: PaperEntryAuthorizationReason.SESSION_BLOCKED,
                    OrderPlacementDecision.GATEWAY_FAILURE: PaperEntryAuthorizationReason.GATEWAY_FAILURE,
                    OrderPlacementDecision.ORDER_REJECTED: PaperEntryAuthorizationReason.ORDER_REJECTED,
                }
                return refused(
                    reasons[result.decision], constructed=True, attempted=True,
                    placement=result.decision.value,
                )
            self._remember(self._seen_entries, identity)
            if lifecycle_number > 1:
                performance_diagnostics.record_entry_counter(
                    "new_lifecycle_authorizations_after_expiry"
                )
            self._active_by_symbol[symbol] = identity
            self._entry_orders[identity] = result.broker_order_id
            performance_diagnostics.record_entry_counter("entry_orders_submitted")
            performance_diagnostics.record_entry_lifecycle(
                identity,
                risk_approved=True,
                submit_timestamp=datetime.now(UTC),
                broker_order_id=result.broker_order_id,
                working_timestamp=datetime.now(UTC),
                terminal_state="WORKING",
            )
            return PaperEntryAuthorizationDecision(
                PaperEntryAuthorizationResult.AUTHORIZED,
                PaperEntryAuthorizationReason.AUTHORIZED, symbol, identity,
                tuple(gates), True, True, result.decision.value,
            )

    def submit_entry(self, signal: object, shares: int, risk_dollars: Decimal) -> bool:
        """Compatibility boundary retaining the historical boolean contract."""

        return self.submit_entry_decision(signal, shares, risk_dollars).authorized

    def submit_add_on(
        self, signal: object, shares: int, risk_dollars: Decimal, *,
        parent_lifecycle_id: str, add_on_id: str,
    ) -> PaperEntryAuthorizationDecision:
        """Submit one explicitly authorized add-on as a correlated LIMIT BUY."""
        symbol = str(getattr(signal, "symbol", "")).strip().upper()
        identity = str(parent_lifecycle_id).strip()
        if not symbol or not identity or not add_on_id or shares <= 0:
            return PaperEntryAuthorizationDecision(
                PaperEntryAuthorizationResult.REFUSED,
                PaperEntryAuthorizationReason.INVALID_QUANTITY, symbol, identity, (),
            )
        with self._lock:
            if self.readiness is not AutonomousPaperReadiness.READY or self.order_book is None:
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.BROKER_NOT_READY, symbol, identity, (),
                )
            if self._authoritative_quantity(symbol) <= 0:
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.POSITION_EXISTS, symbol, identity, (),
                )
            history = self.order_book.history()
            if any(item.request.metadata.get("add_on_id") == add_on_id for item in history):
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.WORKING_ORDER_EXISTS, symbol, identity, (),
                )
            if any(
                not item.is_terminal and item.request.side is OrderSide.BUY
                and item.request.strategy_lifecycle_id == identity
                for item in self.order_book.open_orders_for_symbol(symbol)
            ):
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.WORKING_ORDER_EXISTS, symbol, identity, (),
                )
            request = self.order_command_factory.create_placement_request(
                OrderEntryCommand(
                    symbol=symbol, side="BUY", quantity=Decimal(shares),
                    order_type="LIMIT", limit_price=Decimal(getattr(signal, "entry_trigger")),
                    stop_price=None, time_in_force="DAY",
                    strategy_lifecycle_id=identity,
                    metadata={
                        "source": "autonomous-paper",
                        "reason": "AUTONOMOUS_ADD_ON_ENTRY",
                        "provenance": "AUTONOMOUS_ADD_ON_ENTRY",
                        "lifecycle_id": identity,
                        "parent_lifecycle_id": identity,
                        "add_on_id": add_on_id,
                        "risk_dollars": str(risk_dollars),
                    },
                )
            )
            try:
                result = self.trading_service.place_order(request)
            except Exception:
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.GATEWAY_FAILURE, symbol, identity, (),
                )
            if not result.success:
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.ORDER_REJECTED, symbol, identity, (),
                )
            return PaperEntryAuthorizationDecision(
                PaperEntryAuthorizationResult.AUTHORIZED,
                PaperEntryAuthorizationReason.AUTHORIZED, symbol, identity, (),
                True, True, result.decision.value,
            )

    def submit_rearmed_entry(
        self, signal: object, shares: int, risk_dollars: Decimal, *,
        opportunity_id: str | None = None,
        opportunity_anchor: Decimal | None = None,
        max_lifecycles_per_opportunity: int = 3,
        max_displacement_percent: Decimal = Decimal("1.5"),
        max_displacement_absolute: Decimal = Decimal("0.05"),
        now: datetime | None = None,
    ) -> PaperEntryAuthorizationDecision:
        """Start an explicitly re-armed lifecycle without resetting its anchor."""
        opportunity = opportunity_id or opportunity_identity(signal)
        symbol = str(getattr(signal, "symbol", "")).strip().upper()
        identity = lifecycle_identity(signal)
        if max_lifecycles_per_opportunity <= 0:
            return PaperEntryAuthorizationDecision(
                PaperEntryAuthorizationResult.REFUSED,
                PaperEntryAuthorizationReason.EXPOSURE_LIMIT, symbol, identity, (),
            )
        with self._lock:
            if self.order_book is None:
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.BROKER_NOT_READY, symbol, identity, (),
                )
            if self._authoritative_quantity(symbol) > 0:
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.POSITION_EXISTS, symbol, identity, (),
                )
            if self.order_book.open_orders_for_symbol(symbol):
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.WORKING_ORDER_EXISTS, symbol, identity, (),
                )
            entries = [
                item for item in self.order_book.history()
                if item.request.side is OrderSide.BUY
                and item.request.metadata.get("opportunity_id") == opportunity
            ]
            lifecycle_ids = {
                item.request.strategy_lifecycle_id for item in entries
                if item.request.strategy_lifecycle_id
            }
            if len(lifecycle_ids) >= max_lifecycles_per_opportunity:
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.OPPORTUNITY_LIFECYCLE_LIMIT_EXHAUSTED,
                    symbol, identity, (),
                )
            anchor = opportunity_anchor
            deadline = None
            for item in entries:
                value = item.request.metadata.get("opportunity_original_entry")
                if value is not None:
                    anchor = Decimal(str(value))
                if deadline is None and item.request.metadata.get("opportunity_deadline"):
                    deadline = datetime.fromisoformat(
                        str(item.request.metadata["opportunity_deadline"])
                    )
                if anchor is not None and deadline is not None:
                    break
            if deadline is not None and now is not None and now > deadline:
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.CHASE_WINDOW_EXPIRED,
                    symbol, identity, (),
                )
            trigger = Decimal(str(getattr(signal, "entry_trigger")))
            if anchor is not None and trigger > min(
                anchor * (Decimal("1") + max_displacement_percent / Decimal("100")),
                anchor + max_displacement_absolute,
            ):
                return PaperEntryAuthorizationDecision(
                    PaperEntryAuthorizationResult.REFUSED,
                    PaperEntryAuthorizationReason.EXPOSURE_LIMIT, symbol, identity, (),
                )
            lifecycle_number = len(lifecycle_ids) + 1
        return self.submit_entry_decision(
            signal, shares, risk_dollars, opportunity_id=opportunity,
            opportunity_anchor=anchor, lifecycle_number=lifecycle_number,
            max_lifecycles_per_opportunity=max_lifecycles_per_opportunity,
            max_displacement_percent=max_displacement_percent,
            max_displacement_absolute=max_displacement_absolute,
            provenance="AUTONOMOUS_FAST_MOMENTUM_REARM",
        )

    def replace_entry_order(
        self,
        *,
        lifecycle_id: str,
        replacement_limit: Decimal,
        structural_stop: Decimal,
        original_risk_budget: Decimal,
        account_equity: Decimal,
        buying_power: Decimal,
        existing_exposure: Decimal = Decimal("0"),
        maximum_position_equity_percentage: Decimal = Decimal("0.50"),
        maximum_position_dollars: Decimal = Decimal("25000"),
        maximum_quantity: int = 10000,
        revalidate: Callable[[], bool] | None = None,
        reason: str = "adaptive-entry-replacement",
        now: datetime | None = None,
        max_replacements: int = 2,
    ) -> PaperEntryReplacementDecision:
        """Explicitly replace one working entry after full revalidation.

        This is a dormant execution foundation: no runtime path calls it merely
        because a quote moved.  The predecessor is cancelled and verified
        terminal before a new LIMIT is authorized.  All quantity calculations
        are for the same lifecycle, so a new broker order cannot reset its risk
        budget or duplicate an already-filled quantity.
        """
        identity = str(lifecycle_id).strip()
        normalized_reason = str(reason).strip() or "adaptive-entry-replacement"
        if not identity or replacement_limit <= 0 or structural_stop <= 0:
            return PaperEntryReplacementDecision(
                PaperEntryReplacementState.REFUSED, "", identity,
                "INVALID_REPLACEMENT_INPUT",
            )
        if not all(
            value.is_finite() and value >= 0
            for value in (original_risk_budget, account_equity, buying_power,
                          existing_exposure, maximum_position_dollars)
        ) or not maximum_position_equity_percentage.is_finite():
            return PaperEntryReplacementDecision(
                PaperEntryReplacementState.REFUSED, "", identity,
                "INVALID_ACCOUNT_CONTEXT",
            )
        with self._lock:
            if self.readiness is not AutonomousPaperReadiness.READY:
                return self._replacement_refused(identity, "BROKER_NOT_READY")
            if self.order_book is None:
                return self._replacement_refused(identity, "ORDER_BOOK_UNAVAILABLE")
            symbol = ""
            predecessor_id = self._entry_orders.get(identity)
            predecessor = None
            if self.order_book is not None and predecessor_id is not None:
                try:
                    predecessor = self.order_book.get(predecessor_id)
                except Exception:
                    predecessor = None
            if predecessor is None or predecessor.request.strategy_lifecycle_id != identity:
                return self._replacement_refused(identity, "PREDECESSOR_NOT_FOUND")
            symbol = predecessor.symbol
            if predecessor.request.side is not OrderSide.BUY or predecessor.is_terminal:
                return self._replacement_refused(identity, "PREDECESSOR_NOT_WORKING", symbol)
            if predecessor.request.order_type is not OrderType.LIMIT:
                return self._replacement_refused(identity, "PREDECESSOR_NOT_LIMIT", symbol)
            try:
                sequence = int(predecessor.request.metadata.get("replacement_sequence", 0)) + 1
            except (TypeError, ValueError):
                return self._replacement_refused(identity, "INVALID_REPLACEMENT_SEQUENCE", symbol)
            if sequence > max_replacements:
                return self._replacement_refused(identity, "REPLACEMENT_LIMIT_EXHAUSTED", symbol, predecessor_id, sequence)
            deadline = predecessor.request.metadata.get("chase_deadline")
            if deadline is None:
                deadline = predecessor.request.entry_valid_until
            if deadline is not None and now is not None:
                try:
                    deadline_value = (
                        deadline if isinstance(deadline, datetime)
                        else datetime.fromisoformat(str(deadline))
                    )
                except (TypeError, ValueError):
                    return self._replacement_refused(identity, "INVALID_CHASE_DEADLINE", symbol, predecessor_id, sequence)
                if now > deadline_value:
                    return self._replacement_refused(identity, "CHASE_WINDOW_EXPIRED", symbol, predecessor_id, sequence)
            if revalidate is None:
                return self._replacement_refused(identity, "REVALIDATION_REQUIRED", symbol, predecessor_id, sequence)
            if replacement_limit <= structural_stop:
                return self._replacement_refused(identity, "STRUCTURAL_STOP_INVALID", symbol, predecessor_id, sequence)

            if not self._cancel_entry_predecessor(predecessor):
                return self._replacement_refused(identity, "CANCELLATION_NOT_CONFIRMED", symbol, predecessor_id, sequence)
            try:
                cancelled = self.order_book.get(predecessor.order_id)
            except Exception:
                return self._replacement_refused(identity, "PREDECESSOR_STATE_UNAVAILABLE", symbol, predecessor_id, sequence)
            if not cancelled.is_terminal or cancelled.status.value != "CANCELLED":
                return self._replacement_refused(identity, "CANCELLATION_NOT_CONFIRMED", symbol, predecessor_id, sequence)
            try:
                valid = bool(revalidate())
            except Exception:
                valid = False
            if not valid:
                return self._replacement_refused(identity, "REVALIDATION_FAILED", symbol, predecessor_id, sequence)

            filled, consumed_risk, average_fill = self._lifecycle_fill_state(identity, structural_stop)
            remaining_risk = max(Decimal("0"), original_risk_budget - consumed_risk)
            risk_per_share = replacement_limit - structural_stop
            risk_quantity = int((remaining_risk / risk_per_share).to_integral_value(rounding=ROUND_FLOOR)) if risk_per_share > 0 else 0
            position_quantity = self._authoritative_quantity(symbol)
            predecessor_remaining = int(cancelled.remaining_quantity)
            if self.position_quantity_source is not None:
                position_quantity = Decimal(self.position_quantity_source(symbol))
            if position_quantity < filled:
                return self._replacement_refused(identity, "POSITION_STATE_CONTRADICTS_FILLS", symbol, predecessor_id, sequence)
            notional_room = max(
                Decimal("0"),
                min(maximum_position_dollars, account_equity * maximum_position_equity_percentage)
                - existing_exposure,
            )
            notional_quantity = int((notional_room / replacement_limit).to_integral_value(rounding=ROUND_FLOOR))
            buying_power_quantity = int((buying_power / replacement_limit).to_integral_value(rounding=ROUND_FLOOR))
            quantity = max(0, min(
                predecessor_remaining, risk_quantity, notional_quantity,
                buying_power_quantity, int(maximum_quantity),
            ))
            if quantity <= 0:
                return self._replacement_refused(identity, "REMAINING_AUTHORIZATION_EXHAUSTED", symbol, predecessor_id, sequence)
            original_planned = predecessor.request.metadata.get(
                "original_planned_entry", predecessor.request.limit_price,
            )
            try:
                request = self.order_command_factory.create_placement_request(
                    OrderEntryCommand(
                        symbol=symbol, side="BUY", quantity=Decimal(quantity),
                        order_type="LIMIT", limit_price=replacement_limit,
                        stop_price=None, time_in_force="DAY",
                        strategy_lifecycle_id=identity,
                        metadata={
                            "source": "autonomous-paper",
                            "reason": "ENTRY_REPLACEMENT",
                            "provenance": "AUTONOMOUS_ADAPTIVE_ENTRY_REPLACEMENT",
                            "lifecycle_id": identity,
                            "predecessor_order_id": predecessor.order_id,
                            "replacement_sequence": str(sequence),
                            "replacement_reason": normalized_reason,
                            "original_planned_entry": str(original_planned),
                            "opportunity_id": predecessor.request.metadata.get("opportunity_id"),
                            "opportunity_original_entry": predecessor.request.metadata.get(
                                "opportunity_original_entry", str(original_planned),
                            ),
                            "opportunity_deadline": predecessor.request.metadata.get(
                                "opportunity_deadline",
                            ),
                            "structural_stop": str(structural_stop),
                            "risk_dollars": str(original_risk_budget),
                            "risk_consumed": str(consumed_risk),
                            "cumulative_filled_quantity": str(filled),
                            "remaining_requested_quantity": str(quantity),
                            "entry_validity_seconds": str(int(BAR_INTERVAL.total_seconds())),
                            "chase_deadline": (
                                None if deadline is None else str(deadline)
                            ),
                            "last_entry_mutation_at": (
                                None if now is None else now.isoformat()
                            ),
                        },
                    )
                )
                result = self.trading_service.place_order(request)
            except Exception:
                return self._replacement_refused(identity, "REPLACEMENT_SUBMISSION_FAILED", symbol, predecessor_id, sequence, quantity, filled)
            if not result.success:
                return self._replacement_refused(identity, "REPLACEMENT_REJECTED", symbol, predecessor_id, sequence, quantity, filled)
            replacement_id = result.broker_order_id
            try:
                replacement = self.order_book.get(replacement_id)
            except Exception:
                return self._replacement_refused(identity, "REPLACEMENT_STATE_UNAVAILABLE", symbol, predecessor_id, sequence, quantity, filled)
            if replacement.is_terminal or replacement.request.order_type is not OrderType.LIMIT:
                return self._replacement_refused(identity, "REPLACEMENT_NOT_WORKING_LIMIT", symbol, predecessor_id, sequence, quantity, filled)
            self._entry_orders[identity] = replacement_id
            performance_diagnostics.record_entry_counter("replacements_approved")
            performance_diagnostics.record_entry_counter("replacements_submitted")
            performance_diagnostics.record_entry_counter("replacements_succeeded")
            performance_diagnostics.record_pursuit_evaluation(
                reason="REPLACE_APPROVED",
                symbol=symbol,
                lifecycle_id=identity,
                predecessor_order_id=predecessor_id,
                replacement_order_id=replacement_id,
                replacement_count=sequence,
                requested_quantity=quantity,
                cumulative_filled_quantity=filled,
                timestamp=now or datetime.now(UTC),
            )
            performance_diagnostics.record_entry_lifecycle(
                identity,
                broker_order_id=replacement_id,
                replacement_order_id=replacement_id,
                replacement_timestamp=now or datetime.now(UTC),
                filled_quantity=filled,
                remaining_quantity=quantity,
                replacement_count=sequence,
                terminal_state="WORKING",
            )
            return PaperEntryReplacementDecision(
                PaperEntryReplacementState.SUBMITTED, symbol, identity, "SUBMITTED",
                predecessor_id, replacement_id, sequence, quantity, filled,
            )

    def consider_entry_replacement(
        self,
        *,
        lifecycle_id: str,
        current_ask: Decimal,
        now: datetime,
        structural_stop: Decimal,
        original_risk_budget: Decimal,
        account_equity: Decimal,
        buying_power: Decimal,
        existing_exposure: Decimal = Decimal("0"),
        maximum_position_equity_percentage: Decimal = Decimal("0.50"),
        maximum_position_dollars: Decimal = Decimal("25000"),
        maximum_quantity: int = 10000,
        revalidate: Callable[[], bool] | None = None,
        policy: PaperEntryReplacementPolicy = PaperEntryReplacementPolicy(),
        reason: str = "adaptive-entry-chase",
    ) -> PaperEntryReplacementDecision:
        """Evaluate one fresh observation for a bounded LIMIT replacement.

        This method is intentionally explicit and observation-driven.  Nothing
        calls it on a timer or solely because a quote increased.
        """
        identity = str(lifecycle_id).strip()
        performance_diagnostics.record_entry_counter("replacement_candidates")
        if current_ask <= 0 or now.tzinfo is None or now.utcoffset() is None:
            return self._replacement_refused(identity, "CURRENT_QUOTE_UNAVAILABLE")
        with self._lock:
            predecessor_id = self._entry_orders.get(identity)
            if self.order_book is None or predecessor_id is None:
                return self._replacement_refused(identity, "PREDECESSOR_NOT_FOUND")
            try:
                predecessor = self.order_book.get(predecessor_id)
            except Exception:
                return self._replacement_refused(identity, "PREDECESSOR_NOT_FOUND")
            if predecessor.is_terminal or predecessor.request.order_type is not OrderType.LIMIT:
                return self._replacement_refused(identity, "PREDECESSOR_NOT_WORKING", predecessor.symbol)
            predecessor_limit = predecessor.request.limit_price
            original = predecessor.request.metadata.get(
                "original_planned_entry", predecessor_limit,
            )
            try:
                original_price = Decimal(str(original))
            except Exception:
                return self._replacement_refused(identity, "ORIGINAL_ENTRY_UNAVAILABLE", predecessor.symbol)
            sequence = int(predecessor.request.metadata.get("replacement_sequence", 0))
            if sequence >= policy.max_replacements:
                return self._replacement_refused(identity, "REPLACEMENT_LIMIT_EXHAUSTED", predecessor.symbol, predecessor_id, sequence + 1)
            if predecessor_limit is None or current_ask <= predecessor_limit:
                return self._replacement_refused(identity, "PRICE_NOT_MOVED_ABOVE_PREDECESSOR", predecessor.symbol, predecessor_id, sequence + 1)
            last_mutation = predecessor.request.metadata.get(
                "last_entry_mutation_at",
            ) or predecessor.created_at
            try:
                last_mutation = (
                    last_mutation if isinstance(last_mutation, datetime)
                    else datetime.fromisoformat(str(last_mutation))
                )
            except (TypeError, ValueError):
                return self._replacement_refused(
                    identity, "INVALID_REPRICE_TIMESTAMP", predecessor.symbol,
                )
            if now - last_mutation < timedelta(seconds=float(
                policy.min_reprice_interval_seconds
            )):
                return self._replacement_refused(
                    identity, "REPRICE_INTERVAL_NOT_ELAPSED", predecessor.symbol,
                    predecessor_id, sequence + 1,
                )
            maximum = min(
                original_price * (Decimal("1") + policy.max_displacement_percent / Decimal("100")),
                original_price + policy.max_displacement_absolute,
            )
            if current_ask > maximum:
                return self._replacement_refused(identity, "CHASE_LIMIT_EXCEEDED", predecessor.symbol, predecessor_id, sequence + 1)
            replacement_limit = min(current_ask, maximum)
        return self.replace_entry_order(
            lifecycle_id=identity, replacement_limit=replacement_limit,
            structural_stop=structural_stop, original_risk_budget=original_risk_budget,
            account_equity=account_equity, buying_power=buying_power,
            existing_exposure=existing_exposure,
            maximum_position_equity_percentage=maximum_position_equity_percentage,
            maximum_position_dollars=maximum_position_dollars,
            maximum_quantity=maximum_quantity, revalidate=revalidate,
            reason=reason, now=now, max_replacements=policy.max_replacements,
        )

    consider_adaptive_entry = consider_entry_replacement

    # Explicit alias for callers that name the coordinator operation directly.
    replace_entry = replace_entry_order

    def _cancel_entry_predecessor(self, order: object) -> bool:
        try:
            cancellation = self.trading_service.cancel_order(
                self.order_command_factory.create_cancellation_request(
                    order.order_id, order.request.client_order_id,
                    source="autonomous-paper-entry-replace",
                )
            )
        except Exception:
            return False
        return cancellation is not None and cancellation.success

    def _lifecycle_fill_state(self, identity: str, structural_stop: Decimal) -> tuple[Decimal, Decimal, Decimal | None]:
        filled = Decimal("0")
        notional = Decimal("0")
        if self.order_book is None:
            return filled, Decimal("0"), None
        for order in self.order_book.history():
            if order.request.strategy_lifecycle_id != identity or order.request.side is not OrderSide.BUY:
                continue
            filled += order.filled_quantity
            if order.average_fill_price is not None:
                notional += order.filled_quantity * order.average_fill_price
        average = notional / filled if filled else None
        consumed = Decimal("0") if average is None else filled * max(Decimal("0"), average - structural_stop)
        return filled, consumed, average

    def _replacement_refused(
        self,
        identity: str, reason: str, symbol: str = "", predecessor: str | None = None,
        sequence: int = 0, quantity: int = 0, filled: Decimal = Decimal("0"),
    ) -> PaperEntryReplacementDecision:
        performance_diagnostics.record_pursuit_evaluation(
            reason=reason,
            symbol=symbol,
            lifecycle_id=identity,
            predecessor_order_id=predecessor,
            replacement_count=sequence,
            requested_quantity=quantity,
            cumulative_filled_quantity=filled,
            timestamp=datetime.now(UTC),
        )
        if str(reason).upper() in {
            "REPLACEMENT_SUBMISSION_FAILED", "REPLACEMENT_REJECTED",
            "REPLACEMENT_STATE_UNAVAILABLE", "REPLACEMENT_NOT_WORKING_LIMIT",
        }:
            performance_diagnostics.record_entry_counter("replacements_failed")
        return PaperEntryReplacementDecision(
            PaperEntryReplacementState.REFUSED, symbol, identity, reason,
            predecessor, None, sequence, quantity, filled,
        )

    def submit_exit(
        self, symbol: str, quantity: int, price: Decimal, reason: str,
        lifecycle_id: str | None = None,
    ) -> bool:
        return self.ensure_exit(
            symbol, quantity, price, reason, lifecycle_id,
        ).state is PaperExitSubmissionState.SUBMITTED

    def ensure_exit(
        self, symbol: str, quantity: int, price: Decimal, reason: str,
        lifecycle_id: str | None = None,
    ) -> PaperExitSubmissionDecision:
        normalized = symbol.strip().upper()
        reason_key = reason.strip().upper()
        protective = reason_key in {"STOP", "STOP_LOSS"}
        if protective and quantity > 0:
            performance_diagnostics.record_protection_event(
                state="PROTECTION_REQUIRED",
                symbol=normalized,
                lifecycle_id=lifecycle_id,
                authoritative_open_quantity=quantity,
                stop_price=price,
                reason=reason_key,
            )
        if not self._authorized(normalized) or quantity <= 0 or self.readiness is not AutonomousPaperReadiness.READY:
            return PaperExitSubmissionDecision(
                PaperExitSubmissionState.UNAVAILABLE, normalized,
                lifecycle_id, reason_key,
            )
        if (
            self.position_quantity_source is not None
            and Decimal(self.position_quantity_source(normalized)) < Decimal(quantity)
        ):
            return PaperExitSubmissionDecision(
                PaperExitSubmissionState.UNAVAILABLE, normalized,
                lifecycle_id, reason_key,
            )
        with self._lock:
            self._reconcile_terminal_entries()
            self._reconcile_terminal_exits()
            if self._active_by_symbol.get(normalized, "").startswith("recovered:") and self._authoritative_quantity(normalized) > 0:
                self._management_incomplete.add(normalized)
            active = self._active_by_symbol.get(normalized)
            management_ready = (
                self.management_readiness(normalized)
                is AutonomousManagementReadiness.READY
            )
            # The first protective stop may race the asynchronous capture
            # writer immediately after an entry fill.  The bridge's own
            # active, non-recovered lifecycle is sufficient authority for
            # that stop only; target actions still require persisted context.
            initial_protection_race = (
                reason_key in {"STOP", "STOP_LOSS"}
                and active is not None
                and active == lifecycle_id
                and not active.startswith("recovered:")
            )
            if not management_ready and not initial_protection_race:
                if protective:
                    performance_diagnostics.record_protection_event(
                        state="PROTECTION_SUBMIT_FAILED",
                        symbol=normalized,
                        lifecycle_id=lifecycle_id,
                        authoritative_open_quantity=quantity,
                        protected_quantity=0,
                        stop_price=price,
                        reason="MANAGEMENT_NOT_READY",
                    )
                return PaperExitSubmissionDecision(
                    PaperExitSubmissionState.UNAVAILABLE, normalized,
                    lifecycle_id, reason_key,
                )
            identity = lifecycle_id or active
            if identity is None or active != identity:
                return PaperExitSubmissionDecision(
                    PaperExitSubmissionState.UNAVAILABLE, normalized,
                    identity, reason_key,
                )
            if self.order_book is not None:
                if not self._reconcile_correlated_exits(normalized, identity):
                    self._management_incomplete.add(normalized)
                    return PaperExitSubmissionDecision(
                        PaperExitSubmissionState.UNAVAILABLE, normalized,
                        identity, reason_key,
                    )
                if not self._reconcile_protective_quantity(normalized, identity):
                    self._management_incomplete.add(normalized)
                    return PaperExitSubmissionDecision(
                        PaperExitSubmissionState.UNAVAILABLE, normalized,
                        identity, reason_key,
                    )
                working_sell = next((
                    order for order in self.order_book.open_orders_for_symbol(normalized)
                    if order.request.side is OrderSide.SELL
                    and order.request.strategy_lifecycle_id == identity
                ), None)
                if working_sell is not None:
                    protective = reason_key in {"STOP", "STOP_LOSS"}
                    if not protective and working_sell.request.order_type is OrderType.STOP:
                        return self._place_target_with_correlated_protection(
                            normalized, quantity, price, reason_key, identity,
                            working_sell,
                        )
                    if (
                        protective
                        and working_sell.request.order_type is not OrderType.STOP
                    ):
                        try:
                            cancellation = self.trading_service.cancel_order(
                                self.order_command_factory.create_cancellation_request(
                                    working_sell.order_id,
                                    working_sell.request.client_order_id,
                                    source="autonomous-paper-protective-replace",
                                )
                            )
                        except Exception:
                            cancellation = None
                        if cancellation is None or not cancellation.success:
                            self._management_incomplete.add(normalized)
                            return PaperExitSubmissionDecision(
                                PaperExitSubmissionState.UNAVAILABLE, normalized,
                                identity, reason_key, working_sell.order_id,
                            )
                        self._reconcile_terminal_exits()
                        return self._place_exit(
                            normalized, quantity, price, reason_key, identity,
                        )
                    if protective and working_sell.request.stop_price is not None and Decimal(price) > Decimal(working_sell.request.stop_price):
                        if not self._cancel_working_order(working_sell):
                            self._management_incomplete.add(normalized)
                            return PaperExitSubmissionDecision(
                                PaperExitSubmissionState.UNAVAILABLE, normalized,
                                identity, reason_key, working_sell.order_id,
                            )
                        self._reconcile_terminal_exits()
                        return self._place_exit(
                            normalized, quantity, price, reason_key, identity,
                        )
                    if protective:
                        performance_diagnostics.record_protection_event(
                            state="PROTECTION_ALREADY_PRESENT",
                            symbol=normalized,
                            lifecycle_id=identity,
                            authoritative_open_quantity=quantity,
                            protected_quantity=int(working_sell.remaining_quantity),
                            stop_price=working_sell.request.stop_price,
                            order_id=working_sell.order_id,
                            reason=reason_key,
                        )
                    return PaperExitSubmissionDecision(
                        PaperExitSubmissionState.WORKING, normalized,
                        identity, reason_key, working_sell.order_id,
                        working_sell.created_at,
                    )
            key = (identity, reason_key)
            if key in self._exit_keys:
                return PaperExitSubmissionDecision(
                    PaperExitSubmissionState.UNAVAILABLE, normalized,
                    identity, reason_key, self._exit_orders.get(key),
                )
            return self._place_exit(
                normalized, quantity, price, reason_key, identity,
            )

    def _place_exit(
        self, normalized: str, quantity: int, price: Decimal,
        reason_key: str, identity: str,
    ) -> PaperExitSubmissionDecision:
        protective = reason_key in {"STOP", "STOP_LOSS"}
        if protective:
            performance_diagnostics.record_protection_event(
                state="PROTECTION_SUBMIT_ATTEMPT",
                symbol=normalized,
                lifecycle_id=identity,
                authoritative_open_quantity=quantity,
                protected_quantity=quantity,
                stop_price=price,
                reason=reason_key,
            )
        try:
            result = self.trading_service.place_order(
                self.order_command_factory.create_placement_request(
                    OrderEntryCommand(
                        symbol=normalized, side="SELL", quantity=Decimal(quantity),
                        order_type="STOP" if protective else "LIMIT",
                        limit_price=None if protective else Decimal(price),
                        stop_price=Decimal(price) if protective else None,
                        # Position management must survive DAY rollover.  Entry
                        # validity is handled separately before exposure exists.
                        time_in_force="GTC",
                        strategy_lifecycle_id=identity,
                        metadata={
                            "source": "autonomous-paper",
                            "reason": reason_key,
                            "lifecycle_id": identity,
                        },
                    )
                )
            )
        except Exception as exc:
            if protective:
                performance_diagnostics.record_protection_event(
                    state="PROTECTION_SUBMIT_FAILED",
                    symbol=normalized,
                    lifecycle_id=identity,
                    authoritative_open_quantity=quantity,
                    protected_quantity=0,
                    stop_price=price,
                    reason=type(exc).__name__,
                )
            raise
        if not result.success:
            if protective:
                performance_diagnostics.record_protection_event(
                    state="PROTECTION_SUBMIT_FAILED",
                    symbol=normalized,
                    lifecycle_id=identity,
                    authoritative_open_quantity=quantity,
                    protected_quantity=0,
                    stop_price=price,
                    reason=getattr(result, "decision", None),
                )
            return PaperExitSubmissionDecision(
                PaperExitSubmissionState.UNAVAILABLE, normalized,
                identity, reason_key,
            )
        key = (identity, reason_key)
        self._remember(self._exit_keys, key)
        self._exit_orders[key] = result.broker_order_id
        if protective:
            performance_diagnostics.record_protection_event(
                state="PROTECTION_SUBMIT_ACCEPTED",
                symbol=normalized,
                lifecycle_id=identity,
                authoritative_open_quantity=quantity,
                protected_quantity=quantity,
                stop_price=price,
                order_id=result.broker_order_id,
                reason=reason_key,
            )
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.SUBMITTED, normalized,
            identity, reason_key, result.broker_order_id,
            self._order_created_at(result.broker_order_id),
        )

    def _order_created_at(self, order_id: str | None) -> datetime | None:
        if self.order_book is None or order_id is None:
            return None
        try:
            return self.order_book.get(order_id).created_at
        except Exception:
            return None

    def _cancel_working_order(self, order) -> bool:
        try:
            cancellation = self.trading_service.cancel_order(
                self.order_command_factory.create_cancellation_request(
                    order.order_id,
                    order.request.client_order_id,
                    source="autonomous-paper-correlated-exit",
                )
            )
        except Exception:
            return False
        return cancellation is not None and cancellation.success

    def _place_target_with_correlated_protection(
        self, normalized: str, quantity: int, price: Decimal,
        reason_key: str, identity: str, protective_order,
    ) -> PaperExitSubmissionDecision:
        """Reserve a target quantity while retaining bounded protection.

        A target and a stop may coexist only as one correlated bracket: their
        open quantities must never exceed the authoritative position.  The
        old full-size stop is cancelled before the target is placed, then a
        replacement stop protects the unreserved remainder.
        """
        if self.position_quantity_source is None or not self._cancel_working_order(protective_order):
            return PaperExitSubmissionDecision(
                PaperExitSubmissionState.WORKING, normalized,
                identity, reason_key, protective_order.order_id,
                protective_order.created_at,
            )
        self._exit_orders.pop((identity, "STOP"), None)
        self._exit_keys.pop((identity, "STOP"), None)
        self._reconcile_terminal_exits()
        target = self._place_exit(normalized, quantity, price, reason_key, identity)
        if not target.protection_active:
            self._place_exit(
                normalized,
                int(self.position_quantity_source(normalized)),
                protective_order.request.stop_price,
                "STOP",
                identity,
            )
            return target
        remainder = max(
            0,
            int(self.position_quantity_source(normalized)) - quantity,
        )
        if remainder:
            protection = self._place_exit(
                normalized, remainder, protective_order.request.stop_price,
                "STOP", identity,
            )
            if not protection.protection_active:
                self._management_incomplete.add(normalized)
                try:
                    target_order = self.order_book.get(target.order_id)
                except Exception:
                    target_order = None
                if target_order is not None and not target_order.is_terminal:
                    self._cancel_working_order(target_order)
                self._exit_orders.pop((identity, reason_key), None)
                self._exit_keys.pop((identity, reason_key), None)
                self._reconcile_terminal_exits()
                restored = self._place_exit(
                    normalized,
                    int(self.position_quantity_source(normalized)),
                    protective_order.request.stop_price,
                    "STOP",
                    identity,
                )
                if not restored.protection_active:
                    self._management_incomplete.add(normalized)
                return PaperExitSubmissionDecision(
                    PaperExitSubmissionState.UNAVAILABLE, normalized,
                    identity, reason_key, target.order_id,
                )
        return target

    def _reconcile_correlated_exits(self, normalized: str, identity: str) -> bool:
        """Collapse duplicate working protection before any new exit exists."""
        if self.order_book is None:
            return True
        sells = tuple(
            order for order in self.order_book.open_orders_for_symbol(normalized)
            if order.request.side is OrderSide.SELL
            and order.request.strategy_lifecycle_id == identity
        )
        stops = tuple(order for order in sells if order.request.order_type is OrderType.STOP)
        if len(stops) <= 1:
            return True
        # Keep the newest confirmed protection and retire every predecessor.
        keeper = max(stops, key=lambda order: (order.updated_at, order.order_id))
        for order in stops:
            if order.order_id == keeper.order_id:
                continue
            if not self._cancel_working_order(order):
                return False
        self._reconcile_terminal_exits()
        return all(
            self.order_book.get(order.order_id).is_terminal
            for order in stops if order.order_id != keeper.order_id
        )

    def _reconcile_protective_quantity(self, normalized: str, identity: str) -> bool:
        if self.order_book is None or self.position_quantity_source is None:
            return True
        open_orders = tuple(
            order for order in self.order_book.open_orders_for_symbol(normalized)
            if order.request.side is OrderSide.SELL
            and order.request.strategy_lifecycle_id == identity
        )
        stop = next((order for order in open_orders if order.request.order_type is OrderType.STOP), None)
        if stop is None:
            return True
        reserved = sum(
            (int(order.remaining_quantity) for order in open_orders
             if order.request.order_type is not OrderType.STOP),
            0,
        )
        desired = max(0, int(self.position_quantity_source(normalized)) - reserved)
        if int(stop.remaining_quantity) == desired:
            return True
        if not self._cancel_working_order(stop):
            self._management_incomplete.add(normalized)
            return False
        self._exit_orders.pop((identity, "STOP"), None)
        self._exit_keys.pop((identity, "STOP"), None)
        self._reconcile_terminal_exits()
        if desired:
            replacement = self._place_exit(
                normalized, desired, stop.request.stop_price,
                "STOP", identity,
            )
            return replacement.protection_active
        return True

    def _reconcile_terminal_exits(self) -> None:
        if self.order_book is None:
            return
        for (identity, reason), order_id in tuple(self._exit_orders.items()):
            try:
                order = self.order_book.get(order_id)
            except Exception:
                continue
            if not order.is_terminal:
                continue
            symbol = next(
                (symbol for symbol, value in self._active_by_symbol.items() if value == identity),
                None,
            )
            if symbol is not None and self._authoritative_quantity(symbol) <= 0:
                self._active_by_symbol.pop(symbol, None)
                self._management_incomplete.discard(symbol)
            elif self._authoritative_quantity(symbol) > 0:
                # Any terminal exit that leaves quantity open is incomplete,
                # including a partial terminal fill. Retain position ownership
                # and release only this attempt so management can retry the
                # authoritative remainder deterministically.
                self._exit_orders.pop((identity, reason), None)
                self._exit_keys.pop((identity, reason), None)

    def has_execution_ownership(self, symbol: str) -> bool:
        normalized = symbol.strip().upper()
        with self._lock:
            self._reconcile_terminal_entries()
            self._reconcile_terminal_exits()
            return normalized in self._active_by_symbol

    def _reconcile_terminal_entries(self) -> None:
        if self.order_book is None:
            return
        for identity, order_id in tuple(self._entry_orders.items()):
            try:
                order = self.order_book.get(order_id)
            except Exception:
                continue
            if (
                not order.is_terminal
                or order.filled_quantity > 0
                or self._authoritative_quantity(order.symbol) > 0
            ):
                continue
            if self._active_by_symbol.get(order.symbol) == identity:
                self._active_by_symbol.pop(order.symbol, None)
                self._management_incomplete.discard(order.symbol)
                self._recovered_symbols.discard(order.symbol)
            self._entry_orders.pop(identity, None)

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


__all__ = [
    "AutonomousPaperExecutionBridge", "AutonomousManagementReadiness",
    "AutonomousPaperReadiness", "PaperEntryAuthorizationDecision",
    "PaperEntryAuthorizationReason", "PaperEntryAuthorizationResult",
    "PaperEntryGateDecision", "lifecycle_identity", "opportunity_identity",
    "PaperEntryReplacementDecision", "PaperEntryReplacementState",
    "PaperExitSubmissionDecision", "PaperExitSubmissionState",
]
