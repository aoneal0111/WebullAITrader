from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.broker_protocol.models import (
    BrokerOrderRequest,
    BrokerPosition,
)

from .order_index import OrderIndex


class ReconciliationStatus(StrEnum):
    APPROVED = "APPROVED"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    POSITION_CONFLICT = "POSITION_CONFLICT"


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    approved: bool
    status: ReconciliationStatus
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("reason is required")


class ReconciliationEngine:
    """
    Stateless reconciliation engine.

    Evaluates supplied broker state.
    Performs no broker I/O.
    """

    def evaluate(
        self,
        *,
        request: BrokerOrderRequest,
        orders: OrderIndex,
        positions: tuple[BrokerPosition, ...],
    ) -> ReconciliationDecision:

        if orders.has_open_order(request.symbol):
            return ReconciliationDecision(
                approved=False,
                status=ReconciliationStatus.DUPLICATE_ORDER,
                reason=f"Open order already exists for {request.symbol}.",
            )

        return ReconciliationDecision(
            approved=True,
            status=ReconciliationStatus.APPROVED,
            reason="Broker state reconciled successfully.",
        )

    @staticmethod
    def position_for_symbol(
        symbol: str,
        positions: tuple[BrokerPosition, ...],
    ) -> BrokerPosition | None:
        for position in positions:
            if position.symbol == symbol:
                return position

        return None