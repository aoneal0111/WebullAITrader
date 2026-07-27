"""Paper-only adapters for order placement and cancellation."""

from __future__ import annotations

from app.momentum_scanner import AssetClass
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
    OrderNotFoundError,
    PaperOrderBook,
)
from app.paper_trading.order_models import (
    OrderRequest as PaperOrderRequest,
    OrderSide as PaperOrderSide,
    OrderType as PaperOrderType,
    TimeInForce as PaperTimeInForce,
)
from app.paper_trading.orders import (
    InvalidOrderTransitionError,
    accept_order,
    create_order,
)


class PaperOrderGateway:
    """Adapt deterministic broker commands to the paper order book."""

    def __init__(self, order_book: PaperOrderBook) -> None:
        if not isinstance(order_book, PaperOrderBook):
            raise TypeError("order_book must be PaperOrderBook")

        self._order_book = order_book

    @property
    def order_book(self) -> PaperOrderBook:
        return self._order_book

    def place_order(
        self,
        request: OrderPlacementRequest,
    ) -> BrokerOrderAcknowledgement:
        if not isinstance(request, OrderPlacementRequest):
            raise TypeError(
                "request must be OrderPlacementRequest"
            )

        order = request.order

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

        paper_order = create_order(paper_request)
        paper_order = accept_order(paper_order)

        self._order_book.submit(paper_order)

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
            cancelled = self._order_book.cancel(
                request.broker_order_id
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
            broker_order_id=cancelled.order_id,
            client_order_id=(
                cancelled.request.client_order_id
            ),
            accepted=True,
            message="paper order cancelled",
            metadata={
                "source": "paper_order_gateway",
            },
        )


__all__ = ["PaperOrderGateway"]
