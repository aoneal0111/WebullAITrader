from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.order_compliance.models import OrderSide, OrderType, TradingSession
from app.strategy_engine.models import (
    StrategyDecision,
    StrategyDecisionAction,
)


@dataclass(frozen=True, slots=True)
class StrategyOrderIntent:
    """
    Explicit caller-approved order intent.

    Quantity is deliberately supplied by the caller. The strategy engine
    never derives position size or bypasses risk/compliance validation.
    """

    timestamp: datetime
    request_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    requested_session: TradingSession
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        request_id = self.request_id.strip()

        if not symbol:
            raise ValueError("symbol is required")

        if not request_id:
            raise ValueError("request_id is required")

        if self.quantity <= Decimal("0"):
            raise ValueError("quantity must be positive")

        if (
            self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError(
                "timestamp must be timezone-aware"
            )

        if (
            self.order_type is OrderType.LIMIT
            and self.limit_price is None
        ):
            raise ValueError(
                "limit orders require limit_price"
            )

        if (
            self.order_type is OrderType.STOP
            and self.stop_price is None
        ):
            raise ValueError(
                "stop orders require stop_price"
            )

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "request_id", request_id)


def create_order_intent(
    decision: StrategyDecision,
    *,
    quantity: Decimal,
    request_id: str,
    order_type: OrderType = OrderType.MARKET,
    requested_session: TradingSession = TradingSession.REGULAR,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
) -> StrategyOrderIntent:
    if not decision.creates_order_intent:
        raise ValueError(
            "decision does not create an order intent"
        )

    side = _side_for(decision.action)

    return StrategyOrderIntent(
        timestamp=decision.timestamp,
        request_id=request_id,
        symbol=decision.symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        requested_session=requested_session,
        limit_price=limit_price,
        stop_price=stop_price,
    )


def _side_for(
    action: StrategyDecisionAction,
) -> OrderSide:
    if action in {
        StrategyDecisionAction.ENTER_LONG,
        StrategyDecisionAction.EXIT_SHORT,
    }:
        return OrderSide.BUY

    if action in {
        StrategyDecisionAction.ENTER_SHORT,
        StrategyDecisionAction.EXIT_LONG,
    }:
        return OrderSide.SELL

    raise ValueError(
        f"unsupported executable action: {action.value}"
    )
