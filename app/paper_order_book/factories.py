"""Public construction helpers for Paper Order Book application contracts."""

from datetime import datetime

from app.paper_order_book.models import (
    PaperOrderBookCommand,
    PaperOrderBookIdentity,
    PaperOrderBookObservation,
    PaperOrderBookPolicy,
    PaperOrderBookRequest,
)
from app.paper_trading.order_book_api import PaperOrderBook


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


__all__ = ("create_observation", "create_request")
