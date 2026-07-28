"""Application-facing trading command service.

The service is deliberately thin: deterministic domain runtimes retain all
validation, policy, session, and broker interaction responsibilities.
Presentation clients call this boundary instead of invoking runtimes directly.
"""

from __future__ import annotations

from app.order_cancellation import (
    OrderCancellationRequest,
    OrderCancellationResult,
    OrderCancellationRuntime,
)
from app.order_placement import (
    OrderPlacementRequest,
    OrderPlacementResult,
    OrderPlacementRuntime,
)


class TradingService:
    """Synchronous application boundary for user-initiated trading actions."""

    def __init__(
        self,
        placement_runtime: OrderPlacementRuntime,
        cancellation_runtime: OrderCancellationRuntime,
    ) -> None:
        self._placement_runtime = self._validate_runtime_method(
            placement_runtime,
            "place_order",
            "placement_runtime",
        )
        self._cancellation_runtime = self._validate_runtime_method(
            cancellation_runtime,
            "cancel_order",
            "cancellation_runtime",
        )

    @staticmethod
    def _validate_runtime_method(
        runtime: object,
        method_name: str,
        dependency_name: str,
    ) -> object:
        if runtime is None:
            raise TypeError(f"{dependency_name} must not be None")

        method = getattr(runtime, method_name, None)
        if not callable(method):
            raise TypeError(
                f"{dependency_name} must provide callable {method_name}()"
            )

        return runtime

    def place_order(
        self,
        request: OrderPlacementRequest,
    ) -> OrderPlacementResult:
        """Delegate an immutable placement request to the placement runtime."""

        result = self._placement_runtime.place_order(request)
        if not isinstance(result, OrderPlacementResult):
            raise TypeError(
                "placement_runtime.place_order() must return "
                "OrderPlacementResult"
            )
        return result

    def cancel_order(
        self,
        request: OrderCancellationRequest,
    ) -> OrderCancellationResult:
        """Delegate an immutable cancellation request to the cancellation runtime."""

        result = self._cancellation_runtime.cancel_order(request)
        if not isinstance(result, OrderCancellationResult):
            raise TypeError(
                "cancellation_runtime.cancel_order() must return "
                "OrderCancellationResult"
            )
        return result


__all__ = ["TradingService"]
