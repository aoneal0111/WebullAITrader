"""Public construction helpers for Paper Order Book application contracts."""

from datetime import datetime
from decimal import Decimal

from app.paper_order_book.models import (
    PaperOrderBookCommand,
    PaperOrderBookIdentity,
    PaperOrderBookObservation,
    PaperOrderBookPolicy,
    PaperOrderBookRequest,
)
from app.paper_trading.order_book_api import (
    OrderBookPaperOrder,
    PaperOrderBook,
    create_submission_order,
)


def create_observation(
    *,
    identity: PaperOrderBookIdentity,
    captured_at: datetime,
) -> PaperOrderBookObservation:
    return PaperOrderBookObservation(
        identity=identity,
        order_book=PaperOrderBook(),
        captured_at=captured_at,
    )


def create_request(
    *,
    identity: PaperOrderBookIdentity,
    policy: PaperOrderBookPolicy,
    requested_at: datetime,
    completed_at: datetime,
    commands: tuple[PaperOrderBookCommand, ...] = (),
) -> PaperOrderBookRequest:
    observation = create_observation(
        identity=identity,
        captured_at=completed_at,
    )
    return PaperOrderBookRequest(
        identity=identity,
        snapshot=observation,
        commands=commands,
        requested_at=requested_at,
        completed_at=completed_at,
        policy=policy,
    )


def create_cancel_command(
    *,
    command_id: str,
    order: OrderBookPaperOrder,
    occurred_at: datetime,
) -> PaperOrderBookCommand:
    return PaperOrderBookCommand(
        command_id=command_id,
        command_type="cancel",
        payload=order,
        occurred_at=occurred_at,
    )


def create_submit_command(
    *,
    command_id: str,
    order_id: str,
    occurred_at: datetime,
    symbol: str,
    asset_class: str,
    side: str,
    order_type: str,
    quantity: Decimal,
    time_in_force: str,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    client_order_id: str | None = None,
) -> PaperOrderBookCommand:
    order = create_submission_order(
        order_id=order_id,
        occurred_at=occurred_at,
        symbol=symbol,
        asset_class=asset_class,
        side=side,
        order_type=order_type,
        quantity=quantity,
        time_in_force=time_in_force,
        limit_price=limit_price,
        stop_price=stop_price,
        client_order_id=client_order_id,
    )
    return PaperOrderBookCommand(
        command_id=command_id,
        command_type="submit",
        payload=order,
        occurred_at=occurred_at,
    )


def create_update_command(
    *,
    command_id: str,
    order: OrderBookPaperOrder,
    occurred_at: datetime,
) -> PaperOrderBookCommand:
    return PaperOrderBookCommand(
        command_id=command_id,
        command_type="update",
        payload=order,
        occurred_at=occurred_at,
    )


__all__ = (
    "create_cancel_command",
    "create_observation",
    "create_request",
    "create_submit_command",
    "create_update_command",
)
