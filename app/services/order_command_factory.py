"""Application-level construction of immutable order-placement requests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from app.committee.models import JSONValue
from app.order_placement import (
    OrderPlacementRequest,
    OrderRequestModel,
    OrderSide,
    OrderType,
    TimeInForce,
)


@dataclass(frozen=True, slots=True)
class OrderEntryCommand:
    """Presentation-safe order input.

    Infrastructure-owned values such as account IDs, session IDs, request IDs,
    and client order IDs are deliberately excluded.
    """

    symbol: str
    side: str
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: str
    metadata: Mapping[str, JSONValue] | None = None
    strategy_lifecycle_id: str | None = None


class OrderCommandFactory:
    """Build domain placement requests from presentation-safe commands."""

    def __init__(
        self,
        session_id_provider: Callable[[], str],
        account_id_provider: Callable[[], str],
        *,
        request_id_factory: Callable[[], str] | None = None,
        client_order_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not callable(session_id_provider):
            raise TypeError("session_id_provider must be callable")

        if not callable(account_id_provider):
            raise TypeError("account_id_provider must be callable")

        if request_id_factory is not None and not callable(
            request_id_factory
        ):
            raise TypeError("request_id_factory must be callable")

        if client_order_id_factory is not None and not callable(
            client_order_id_factory
        ):
            raise TypeError("client_order_id_factory must be callable")

        self._session_id_provider = session_id_provider
        self._account_id_provider = account_id_provider
        self._request_id_factory = (
            request_id_factory or self._generate_request_id
        )
        self._client_order_id_factory = (
            client_order_id_factory or self._generate_client_order_id
        )

    def create_placement_request(
        self,
        command: OrderEntryCommand,
    ) -> OrderPlacementRequest:
        """Convert UI-safe input into a validated domain request."""

        if not isinstance(command, OrderEntryCommand):
            raise TypeError("command must be OrderEntryCommand")

        session_id = self._resolve_identifier(
            self._session_id_provider,
            "session_id_provider",
        )
        account_id = self._resolve_identifier(
            self._account_id_provider,
            "account_id_provider",
        )
        request_id = self._resolve_identifier(
            self._request_id_factory,
            "request_id_factory",
        )
        client_order_id = self._resolve_identifier(
            self._client_order_id_factory,
            "client_order_id_factory",
        )

        order = OrderRequestModel(
            request_id=request_id,
            account_id=account_id,
            symbol=command.symbol.strip().upper(),
            side=OrderSide(command.side),
            order_type=OrderType(command.order_type),
            quantity=command.quantity,
            limit_price=command.limit_price,
            stop_price=command.stop_price,
            time_in_force=TimeInForce(command.time_in_force),
            client_order_id=client_order_id,
            strategy_lifecycle_id=command.strategy_lifecycle_id,
            metadata=dict(command.metadata or {}),
        )

        return OrderPlacementRequest(
            session_id=session_id,
            order=order,
            metadata={
                "source": "desktop_order_entry",
            },
        )

    @staticmethod
    def _resolve_identifier(
        provider: Callable[[], str],
        provider_name: str,
    ) -> str:
        value = provider()

        if not isinstance(value, str):
            raise TypeError(
                f"{provider_name} must return a string"
            )

        value = value.strip()

        if not value:
            raise ValueError(
                f"{provider_name} returned an empty identifier"
            )

        return value

    @staticmethod
    def _generate_request_id() -> str:
        return f"request-{uuid4().hex}"

    @staticmethod
    def _generate_client_order_id() -> str:
        return f"client-{uuid4().hex}"


__all__ = [
    "OrderCommandFactory",
    "OrderEntryCommand",
]
