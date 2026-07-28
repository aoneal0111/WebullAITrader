from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import TypeVar, cast

from app.operations_core.events import OperationsEvent


EventT = TypeVar("EventT", bound=OperationsEvent)
EventHandler = Callable[[EventT], None]


@dataclass(frozen=True, slots=True)
class Subscription:
    """Opaque subscription returned by OperationsBus.subscribe."""

    event_type: type[OperationsEvent]
    subscription_id: int


class OperationsBus:
    """
    Synchronous, thread-safe event distributor.

    Handlers run in publication order on the publishing thread. The bus owns no
    trading logic and does not silently suppress handler failures.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._next_subscription_id = 1
        self._handlers: dict[
            type[OperationsEvent],
            dict[int, EventHandler[OperationsEvent]],
        ] = defaultdict(dict)

    def subscribe(
        self,
        event_type: type[EventT],
        handler: EventHandler[EventT],
    ) -> Subscription:
        if not isinstance(event_type, type):
            raise TypeError("event_type must be an event class")

        if not issubclass(event_type, OperationsEvent):
            raise TypeError("event_type must inherit OperationsEvent")

        if not callable(handler):
            raise TypeError("handler must be callable")

        with self._lock:
            subscription_id = self._next_subscription_id
            self._next_subscription_id += 1

            self._handlers[event_type][subscription_id] = cast(
                EventHandler[OperationsEvent],
                handler,
            )

        return Subscription(
            event_type=event_type,
            subscription_id=subscription_id,
        )

    def unsubscribe(self, subscription: Subscription) -> bool:
        with self._lock:
            handlers = self._handlers.get(subscription.event_type)

            if handlers is None:
                return False

            removed = handlers.pop(subscription.subscription_id, None)

            if not handlers:
                self._handlers.pop(subscription.event_type, None)

            return removed is not None

    def publish(self, event: EventT) -> None:
        if not isinstance(event, OperationsEvent):
            raise TypeError("event must inherit OperationsEvent")

        with self._lock:
            matching_handlers = (
                (subscription_id, handler)
                for registered_type, registered_handlers in self._handlers.items()
                if isinstance(event, registered_type)
                for subscription_id, handler in registered_handlers.items()
            )
            handlers = tuple(
                handler
                for _, handler in sorted(
                    matching_handlers,
                    key=lambda item: item[0],
                )
            )

        for handler in handlers:
            handler(event)

    @property
    def subscription_count(self) -> int:
        with self._lock:
            return sum(len(handlers) for handlers in self._handlers.values())
