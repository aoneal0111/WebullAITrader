"""Application orchestrator for public paper-order lifecycle operations."""

import app.paper_trading.order_book_api as lifecycle_api

from app.paper_order_book.exceptions import PaperOrderBookValidationError
from app.paper_order_book.models import (
    PaperOrderBookCommand,
    PaperOrderBookObservation,
    PaperOrderBookRequest,
    PaperOrderBookResult,
    PaperOrderBookSummary,
)
from app.paper_order_book.runtime import PaperOrderBookRuntime


class PaperOrderBookOrchestrator:
    """Validate first, then delegate mutations to the lifecycle facade."""

    def __init__(self, runtime: PaperOrderBookRuntime | None = None) -> None:
        if runtime is None:
            from app.paper_order_book.composition import create_runtime

            runtime = create_runtime()
        self._runtime = runtime

    def execute(
        self,
        request: PaperOrderBookRequest,
    ) -> PaperOrderBookResult:
        evaluated = self._runtime.evaluate(request)
        if not evaluated.criteria.accepted:
            return evaluated

        order_book = request.snapshot.order_book
        for command in request.commands:
            self._dispatch(order_book, command)

        observation = PaperOrderBookObservation(
            identity=request.snapshot.identity,
            order_book=order_book,
            captured_at=request.completed_at,
        )
        summary = PaperOrderBookSummary(
            initial_orders=len(order_book),
            command_count=len(request.commands),
        )
        return PaperOrderBookResult(
            identity=request.identity,
            snapshot=observation,
            commands=request.commands,
            summary=summary,
            criteria=evaluated.criteria,
            requested_at=request.requested_at,
            completed_at=request.completed_at,
            errors=evaluated.errors,
        )

    @staticmethod
    def _dispatch(
        order_book: lifecycle_api.PaperOrderBook,
        command: PaperOrderBookCommand,
    ) -> None:
        payload = command.payload
        if command.command_type == "submit" and isinstance(
            payload, lifecycle_api.OrderBookPaperOrder
        ):
            lifecycle_api.submit(order_book, payload)
            return
        if command.command_type == "update" and isinstance(
            payload, lifecycle_api.OrderBookPaperOrder
        ):
            lifecycle_api.update(order_book, payload)
            return
        if command.command_type == "cancel" and isinstance(
            payload, lifecycle_api.OrderBookPaperOrder
        ):
            lifecycle_api.cancel(
                order_book,
                payload,
                at=command.occurred_at,
            )
            return
        if command.command_type == "accept" and isinstance(
            payload, lifecycle_api.OrderBookPaperOrder
        ):
            lifecycle_api.accept(
                order_book,
                payload,
                at=command.occurred_at,
            )
            return
        if command.command_type == "expire" and isinstance(
            payload, lifecycle_api.OrderBookPaperOrder
        ):
            lifecycle_api.expire(
                order_book,
                payload,
                at=command.occurred_at,
            )
            return
        if command.command_type == "expire_day_orders" and payload is order_book:
            lifecycle_api.expire_day_orders(
                order_book,
                at=command.occurred_at,
            )
            return
        if command.command_type == "apply_fill" and isinstance(
            payload, lifecycle_api.OrderBookFill
        ):
            lifecycle_api.record_fill(order_book, payload)
            return
        raise PaperOrderBookValidationError(
            f"unsupported command payload for command_type: "
            f"{command.command_type}"
        )


__all__ = ("PaperOrderBookOrchestrator",)
