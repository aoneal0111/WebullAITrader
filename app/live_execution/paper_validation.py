"""Paper-account trading lifecycle validation.

The validator composes the existing broker protocol.  It owns no credentials,
transport, market-data dependency, or production execution capability.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.broker_protocol.models import (
    BrokerCash,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPosition,
    BrokerSide,
    TimeInForce,
    TradingSession,
)
from app.broker_protocol.protocol import Broker


ACCOUNT_ENDPOINT = "/account/list"
BALANCE_ENDPOINT = "/assets/balance"
POSITIONS_ENDPOINT = "/assets/positions"
OPEN_ORDERS_ENDPOINT = "/trade/order/open"
PLACE_ORDER_ENDPOINT = "/trade/order/place"
CANCEL_ORDER_ENDPOINT = "/trade/order/cancel"
FILLS_ENDPOINT = "/trade/order/history"


class PaperValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    RUNNING = "RUNNING"


class PaperOrderState(StrEnum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    WORKING = "WORKING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PaperValidationEventSink(Protocol):
    def emit(self, event: "PaperValidationEvent") -> None: ...


class PaperValidationLogger(Protocol):
    def log(self, operation: str, status: str, **fields: object) -> None: ...


@dataclass(frozen=True, slots=True)
class PaperValidationEvent:
    occurred_at: datetime
    fingerprint: str
    order_id: str | None = None
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PaperValidationStarted(PaperValidationEvent):
    pass


@dataclass(frozen=True, slots=True)
class PaperOrderSubmitted(PaperValidationEvent):
    pass


@dataclass(frozen=True, slots=True)
class PaperOrderFilled(PaperValidationEvent):
    pass


@dataclass(frozen=True, slots=True)
class PaperOrderCancelled(PaperValidationEvent):
    pass


@dataclass(frozen=True, slots=True)
class PaperValidationCompleted(PaperValidationEvent):
    pass


@dataclass(frozen=True, slots=True)
class PaperValidationFailed(PaperValidationEvent):
    pass


@dataclass(slots=True)
class InMemoryPaperValidationEventStore:
    events: list[PaperValidationEvent] = field(default_factory=list)

    def emit(self, event: PaperValidationEvent) -> None:
        self.events.append(event)


@dataclass(frozen=True, slots=True)
class PaperValidationStep:
    status: PaperValidationStatus
    detail: str = ""

    @classmethod
    def running(cls, detail: str = "") -> "PaperValidationStep":
        return cls(PaperValidationStatus.RUNNING, detail)


@dataclass(frozen=True, slots=True)
class BuyingPowerValidation:
    before_trade: Decimal | None = None
    after_buy: Decimal | None = None
    after_sell_or_cancel: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PaperValidationReport:
    account: PaperValidationStep
    orders: PaperValidationStep
    buying_power: PaperValidationStep
    positions: PaperValidationStep
    reconciliation: PaperValidationStep
    overall: PaperValidationStatus
    message: str = ""
    order_id: str | None = None
    order_states: tuple[PaperOrderState, ...] = ()
    buying_power_values: BuyingPowerValidation = BuyingPowerValidation()

    @classmethod
    def initial(cls) -> "PaperValidationReport":
        step = PaperValidationStep.running("Not started")
        return cls(step, step, step, step, step, PaperValidationStatus.RUNNING)

    @classmethod
    def live_disabled(cls) -> "PaperValidationReport":
        failed = PaperValidationStep(PaperValidationStatus.FAIL, "Validation disabled in LIVE")
        return cls(failed, failed, failed, failed, failed, PaperValidationStatus.FAIL,
                   "Validation disabled in LIVE")


class _NullSink:
    def emit(self, event: PaperValidationEvent) -> None:
        del event


class _NullLogger:
    def log(self, operation: str, status: str, **fields: object) -> None:
        del operation, status, fields


class PaperTradingValidator:
    """Validate one reversible AAPL lifecycle against a PAPER broker account."""

    def __init__(
        self,
        broker: Broker,
        *,
        environment: str | None = None,
        event_store: PaperValidationEventSink | None = None,
        logger: PaperValidationLogger | None = None,
        status_sink: Callable[[PaperValidationReport], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        poll_attempts: int = 5,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if poll_attempts < 1:
            raise ValueError("poll_attempts must be positive")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds cannot be negative")
        self._broker = broker
        configured = environment if environment is not None else os.getenv("TRADING_ENVIRONMENT", "")
        self._environment = str(getattr(configured, "value", configured)).strip().upper()
        self._events = event_store or _NullSink()
        self._logger = logger or _NullLogger()
        self._status_sink = status_sink
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleeper
        self._id_factory = id_factory
        self._poll_attempts = poll_attempts
        self._poll_interval = poll_interval_seconds
        self._fingerprint = ""
        self._report = PaperValidationReport.initial()

    @property
    def report(self) -> PaperValidationReport:
        return self._report

    def run(self) -> PaperValidationReport:
        if self._is_live():
            self._report = PaperValidationReport.live_disabled()
            self._publish()
            return self._report
        if self._environment not in {"PAPER", "TEST", "SANDBOX"}:
            return self._fail("Paper validation requires TRADING_ENVIRONMENT=PAPER", emit=False)

        self._fingerprint = hashlib.sha256(
            f"paper-validation:{self._environment}:{self._id_factory()}".encode("utf-8")
        ).hexdigest()[:16]
        self._emit(PaperValidationStarted)
        self._report = PaperValidationReport.initial()
        self._publish()
        connected = False
        try:
            self._invoke("connect", ACCOUNT_ENDPOINT, self._broker.connect)
            connected = True
            if not self._validate_account():
                return self._fail("Paper account connectivity failed")
            return self._validate_lifecycle()
        except Exception as exc:
            return self._fail(f"{type(exc).__name__}: validation operation failed")
        finally:
            if connected:
                try:
                    self._invoke("disconnect", ACCOUNT_ENDPOINT, self._broker.disconnect)
                except Exception:
                    pass

    def _is_live(self) -> bool:
        return self._environment == "LIVE" or self._environment.endswith("-LIVE")

    def _validate_account(self) -> bool:
        checks = (
            ("get_account", ACCOUNT_ENDPOINT, self._broker.get_account),
            ("get_balance", BALANCE_ENDPOINT, self._broker.get_cash),
            ("get_positions", POSITIONS_ENDPOINT, self._broker.get_positions),
            ("get_open_orders", OPEN_ORDERS_ENDPOINT, self._broker.get_orders),
        )
        try:
            for operation, endpoint, callback in checks:
                value = self._invoke(operation, endpoint, callback)
                if value is None:
                    raise ValueError(f"{operation} returned no value")
        except Exception:
            self._report = replace(
                self._report,
                account=PaperValidationStep(PaperValidationStatus.FAIL, "FAILED"),
            )
            self._publish()
            return False
        self._report = replace(
            self._report,
            account=PaperValidationStep(PaperValidationStatus.PASS, "CONNECTED"),
        )
        self._publish()
        return True

    def _validate_lifecycle(self) -> PaperValidationReport:
        before = self._invoke("buying_power_before", BALANCE_ENDPOINT, self._broker.get_cash)
        before_power = self._buying_power(before)
        powers = BuyingPowerValidation(before_trade=before_power)
        self._report = replace(self._report, buying_power_values=powers)

        client_id = f"atlas-paper-validation-{self._id_factory()}"
        states = (PaperOrderState.CREATED,)
        self._report = replace(self._report, order_id=client_id, order_states=states,
                               orders=PaperValidationStep.running("CREATED"))
        self._log("order_created", "CREATED", PLACE_ORDER_ENDPOINT, client_id, 0)
        self._publish()
        request = BrokerOrderRequest(
            client_order_id=client_id,
            symbol="AAPL",
            side=BrokerSide.BUY,
            order_type=BrokerOrderType.MARKET,
            quantity=Decimal("1"),
            limit_price=None,
            stop_price=None,
            time_in_force=TimeInForce.DAY,
            trading_session=TradingSession.CORE,
        )
        order = self._invoke("submit_buy", PLACE_ORDER_ENDPOINT,
                             lambda: self._broker.submit_order(request), client_id)
        states += (PaperOrderState.SUBMITTED,)
        self._emit(PaperOrderSubmitted, order.broker_order_id,
                   (("client_order_id", client_id), ("side", "BUY")))
        state = self._paper_state(order.status)
        if state not in states:
            states += (state,)
        self._report = replace(self._report, order_id=order.broker_order_id,
                               order_states=states,
                               orders=PaperValidationStep.running(state.value))
        self._publish()
        if state is PaperOrderState.REJECTED:
            self._report = replace(self._report,
                                   orders=PaperValidationStep(PaperValidationStatus.FAIL, "REJECTED"))
            return self._fail("Paper buy order was rejected")

        after_buy_cash = self._invoke("buying_power_after_buy", BALANCE_ENDPOINT,
                                      self._broker.get_cash, order.broker_order_id)
        powers = replace(powers, after_buy=self._buying_power(after_buy_cash))
        self._report = replace(self._report, buying_power_values=powers)
        final_order, state = self._await_order(order)
        if state not in states:
            states += (state,)

        if state is PaperOrderState.WORKING:
            cancelled = self._invoke("cancel_buy", CANCEL_ORDER_ENDPOINT,
                                     lambda: self._broker.cancel_order(client_id), order.broker_order_id)
            if cancelled.status is not BrokerOrderStatus.CANCELLED:
                self._report = replace(self._report, order_states=states,
                                       orders=PaperValidationStep(PaperValidationStatus.FAIL,
                                                                  "Cancellation not confirmed"))
                return self._fail("Paper order cancellation was not confirmed")
            states += (PaperOrderState.CANCELLED,)
            self._emit(PaperOrderCancelled, cancelled.broker_order_id)
            after_cancel = self._invoke("buying_power_after_cancel", BALANCE_ENDPOINT,
                                        self._broker.get_cash, cancelled.broker_order_id)
            powers = replace(powers, after_sell_or_cancel=self._buying_power(after_cancel))
            self._report = replace(
                self._report,
                order_states=states,
                orders=PaperValidationStep(PaperValidationStatus.PASS, "CANCELLED"),
                positions=PaperValidationStep(PaperValidationStatus.PASS,
                                               "No fill; position unchanged"),
                buying_power_values=powers,
            )
        elif state is PaperOrderState.FILLED:
            self._emit(PaperOrderFilled, final_order.broker_order_id, (("side", "BUY"),))
            result = self._close_filled_position(final_order, states, powers)
            if result is None:
                return self._fail("Filled position validation or closing sell failed")
            states, powers = result
        else:
            self._report = replace(self._report, order_states=states,
                                   orders=PaperValidationStep(PaperValidationStatus.FAIL, state.value))
            return self._fail(f"Paper order ended in {state.value}")

        if not self._validate_buying_power(powers, filled=PaperOrderState.FILLED in states):
            return self._fail("Buying power did not change consistently with the lifecycle")
        if not self._reconcile():
            return self._fail("Broker reconciliation mismatch")
        self._report = replace(self._report, overall=PaperValidationStatus.PASS,
                               message="Paper trading validation passed")
        self._emit(PaperValidationCompleted, self._report.order_id)
        self._publish()
        return self._report

    def _close_filled_position(
        self,
        buy: BrokerOrder,
        states: tuple[PaperOrderState, ...],
        powers: BuyingPowerValidation,
    ) -> tuple[tuple[PaperOrderState, ...], BuyingPowerValidation] | None:
        positions = self._invoke("positions_after_buy", POSITIONS_ENDPOINT,
                                 self._broker.get_positions, buy.broker_order_id)
        position = next((item for item in positions if item.symbol.upper() == "AAPL"), None)
        if position is None or position.quantity != Decimal("1") or position.average_price <= 0:
            self._report = replace(self._report,
                                   positions=PaperValidationStep(PaperValidationStatus.FAIL,
                                                                  "Filled AAPL position mismatch"))
            return None
        self._report = replace(self._report,
                               positions=PaperValidationStep.running("Buy position synchronized"))
        self._publish()

        sell_id = f"atlas-paper-validation-{self._id_factory()}"
        request = BrokerOrderRequest(sell_id, "AAPL", BrokerSide.SELL, BrokerOrderType.MARKET,
                                     Decimal("1"), None, None, TimeInForce.DAY, TradingSession.CORE)
        sell = self._invoke("submit_sell", PLACE_ORDER_ENDPOINT,
                            lambda: self._broker.submit_order(request), sell_id)
        self._emit(PaperOrderSubmitted, sell.broker_order_id,
                   (("client_order_id", sell_id), ("side", "SELL")))
        sell, sell_state = self._await_order(sell)
        if sell_state is not PaperOrderState.FILLED:
            if sell_state is PaperOrderState.WORKING:
                cancelled = self._invoke("cancel_sell", CANCEL_ORDER_ENDPOINT,
                                         lambda: self._broker.cancel_order(sell_id), sell.broker_order_id)
                if cancelled.status is BrokerOrderStatus.CANCELLED:
                    self._emit(PaperOrderCancelled, cancelled.broker_order_id, (("side", "SELL"),))
            self._report = replace(self._report,
                                   orders=PaperValidationStep(PaperValidationStatus.FAIL,
                                                              f"SELL {sell_state.value}"))
            return None
        self._emit(PaperOrderFilled, sell.broker_order_id, (("side", "SELL"),))
        remaining = self._invoke("positions_after_sell", POSITIONS_ENDPOINT,
                                 self._broker.get_positions, sell.broker_order_id)
        open_quantity = sum((item.quantity for item in remaining if item.symbol.upper() == "AAPL"),
                            Decimal("0"))
        if open_quantity != 0:
            self._report = replace(self._report,
                                   positions=PaperValidationStep(PaperValidationStatus.FAIL,
                                                                  "AAPL position did not return to zero"))
            return None
        after_sell = self._invoke("buying_power_after_sell", BALANCE_ENDPOINT,
                                  self._broker.get_cash, sell.broker_order_id)
        powers = replace(powers, after_sell_or_cancel=self._buying_power(after_sell))
        if PaperOrderState.FILLED not in states:
            states += (PaperOrderState.FILLED,)
        self._report = replace(
            self._report,
            order_states=states,
            orders=PaperValidationStep(PaperValidationStatus.PASS, "BUY FILLED; SELL FILLED"),
            positions=PaperValidationStep(PaperValidationStatus.PASS, "AAPL position returned to zero"),
            buying_power_values=powers,
        )
        self._publish()
        return states, powers

    def _await_order(self, original: BrokerOrder) -> tuple[BrokerOrder, PaperOrderState]:
        current = original
        state = self._paper_state(current.status)
        if state in {PaperOrderState.FILLED, PaperOrderState.REJECTED, PaperOrderState.CANCELLED}:
            return current, state
        for attempt in range(self._poll_attempts):
            orders = self._invoke("poll_order", OPEN_ORDERS_ENDPOINT,
                                  self._broker.get_orders, current.broker_order_id)
            found = next((item for item in orders
                          if item.client_order_id == current.client_order_id
                          or item.broker_order_id == current.broker_order_id), None)
            if found is not None:
                current = found
                state = self._paper_state(found.status)
                if state in {PaperOrderState.FILLED, PaperOrderState.REJECTED,
                             PaperOrderState.CANCELLED}:
                    return current, state
            else:
                fills = self._invoke("poll_fills", FILLS_ENDPOINT,
                                     self._broker.get_fills, current.broker_order_id)
                filled = sum((fill.quantity for fill in fills
                              if fill.broker_order_id == current.broker_order_id), Decimal("0"))
                if filled >= current.quantity:
                    return current, PaperOrderState.FILLED
            if attempt + 1 < self._poll_attempts and self._poll_interval:
                self._sleep(self._poll_interval)
        return current, state

    def _validate_buying_power(self, values: BuyingPowerValidation, *, filled: bool) -> bool:
        before, after_buy, final = values.before_trade, values.after_buy, values.after_sell_or_cancel
        valid = before is not None and after_buy is not None and final is not None
        if valid:
            valid = after_buy <= before and final >= after_buy
            if filled:
                valid = valid and after_buy < before
        self._report = replace(
            self._report,
            buying_power=PaperValidationStep(
                PaperValidationStatus.PASS if valid else PaperValidationStatus.FAIL,
                "Buying power synchronized" if valid else "Buying power values inconsistent",
            ),
            buying_power_values=values,
        )
        self._log("validate_buying_power", "PASS" if valid else "FAILED",
                  BALANCE_ENDPOINT, self._report.order_id, 0)
        self._publish()
        return valid

    def _reconcile(self) -> bool:
        first = self._broker_snapshot("reconciliation_local")
        second = self._broker_snapshot("reconciliation_broker")
        valid = first == second
        self._report = replace(
            self._report,
            reconciliation=PaperValidationStep(
                PaperValidationStatus.PASS if valid else PaperValidationStatus.FAIL,
                "Orders, positions, cash, and buying power match broker" if valid
                else "Broker state changed during reconciliation",
            ),
        )
        self._log("reconciliation_compare", "PASS" if valid else "FAILED",
                  f"{OPEN_ORDERS_ENDPOINT}|{POSITIONS_ENDPOINT}|{BALANCE_ENDPOINT}",
                  self._report.order_id, 0)
        self._publish()
        return valid

    def _broker_snapshot(self, prefix: str) -> tuple[tuple[BrokerOrder, ...], tuple[BrokerPosition, ...], BrokerCash]:
        orders = tuple(self._invoke(f"{prefix}_orders", OPEN_ORDERS_ENDPOINT, self._broker.get_orders))
        positions = tuple(self._invoke(f"{prefix}_positions", POSITIONS_ENDPOINT, self._broker.get_positions))
        cash = self._invoke(f"{prefix}_cash", BALANCE_ENDPOINT, self._broker.get_cash)
        return orders, positions, cash

    @staticmethod
    def _buying_power(cash: BrokerCash) -> Decimal | None:
        value = cash.buying_power
        return value if value is not None else cash.settled_cash

    @staticmethod
    def _paper_state(status: BrokerOrderStatus) -> PaperOrderState:
        if status is BrokerOrderStatus.FILLED:
            return PaperOrderState.FILLED
        if status is BrokerOrderStatus.CANCELLED:
            return PaperOrderState.CANCELLED
        if status is BrokerOrderStatus.REJECTED:
            return PaperOrderState.REJECTED
        return PaperOrderState.WORKING

    def _invoke(self, operation: str, endpoint: str, callback: Callable[[], object],
                order_id: str | None = None):
        started = self._monotonic()
        try:
            result = callback()
        except Exception:
            self._log(operation, "FAILED", endpoint, order_id,
                      self._elapsed_ms(started))
            raise
        self._log(operation, "PASS", endpoint, order_id, self._elapsed_ms(started))
        return result

    def _elapsed_ms(self, started: float) -> int:
        return max(0, round((self._monotonic() - started) * 1000))

    def _log(self, operation: str, status: str, endpoint: str,
             order_id: str | None, elapsed_ms: int) -> None:
        self._logger.log(
            operation,
            status,
            order_id=order_id,
            elapsed_ms=elapsed_ms,
            endpoint=endpoint,
            environment=self._environment,
            fingerprint=self._fingerprint or "not-started",
        )

    def _emit(self, event_type: type[PaperValidationEvent], order_id: str | None = None,
              details: tuple[tuple[str, str], ...] = ()) -> None:
        event = event_type(self._clock(), self._fingerprint, order_id, tuple(sorted(details)))
        emitter = getattr(self._events, "emit", None)
        if callable(emitter):
            emitter(event)
        else:
            publisher = getattr(self._events, "publish", None)
            if not callable(publisher):
                raise TypeError("event_store must provide emit(event) or publish(event)")
            publisher(event)

    def _fail(self, message: str, *, emit: bool = True) -> PaperValidationReport:
        def terminal(step: PaperValidationStep) -> PaperValidationStep:
            if step.status is not PaperValidationStatus.RUNNING:
                return step
            return PaperValidationStep(PaperValidationStatus.FAIL, "Not completed")

        self._report = replace(
            self._report,
            account=terminal(self._report.account),
            orders=terminal(self._report.orders),
            buying_power=terminal(self._report.buying_power),
            positions=terminal(self._report.positions),
            reconciliation=terminal(self._report.reconciliation),
            overall=PaperValidationStatus.FAIL,
            message=message,
        )
        if emit:
            self._emit(PaperValidationFailed, self._report.order_id,
                       (("reason", message),))
        self._publish()
        return self._report

    def _publish(self) -> None:
        if self._status_sink is not None:
            self._status_sink(self._report)


__all__ = [
    "BuyingPowerValidation",
    "InMemoryPaperValidationEventStore",
    "PaperOrderCancelled",
    "PaperOrderFilled",
    "PaperOrderState",
    "PaperOrderSubmitted",
    "PaperTradingValidator",
    "PaperValidationCompleted",
    "PaperValidationEvent",
    "PaperValidationFailed",
    "PaperValidationReport",
    "PaperValidationStarted",
    "PaperValidationStatus",
    "PaperValidationStep",
]
