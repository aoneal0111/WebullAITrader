from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import timedelta

from app.order_compliance.models import OrderSide, OrderType, ProposedOrder
from app.paper_trading.models import ExecutionStatus, PaperExecutionConfig, PaperMarketQuote


@dataclass(frozen=True, slots=True)
class FillEvaluation:
    status: ExecutionStatus
    reason: str
    fill_price: Decimal | None


def evaluate_fill(
    order: ProposedOrder, quote: PaperMarketQuote, config: PaperExecutionConfig
) -> FillEvaluation:
    if not isinstance(config.maximum_quote_age_seconds, int) or isinstance(config.maximum_quote_age_seconds, bool) or config.maximum_quote_age_seconds < 0:
        return FillEvaluation(ExecutionStatus.REJECTED, "Quote-age configuration is malformed.", None)
    if quote.timestamp.tzinfo is None or order.created_timestamp.tzinfo is None:
        return FillEvaluation(ExecutionStatus.REJECTED, "Quote and proposal timestamps must be timezone-aware.", None)
    age = abs(order.created_timestamp - quote.timestamp)
    if age > timedelta(seconds=config.maximum_quote_age_seconds):
        return FillEvaluation(ExecutionStatus.REJECTED, "Quote is stale for the configured maximum age.", None)
    if quote.symbol.strip().upper() != order.symbol.strip().upper():
        return FillEvaluation(ExecutionStatus.REJECTED, "Quote symbol does not match proposal.", None)
    if order.order_type not in (OrderType.MARKET, OrderType.LIMIT):
        return FillEvaluation(ExecutionStatus.REJECTED, "Only MARKET and LIMIT are supported.", None)
    price = quote.ask if order.side is OrderSide.BUY else quote.bid
    if not _positive(price):
        required = "ask" if order.side is OrderSide.BUY else "bid"
        return FillEvaluation(ExecutionStatus.REJECTED, f"A valid {required} is required.", None)
    if order.order_type is OrderType.MARKET:
        return FillEvaluation(ExecutionStatus.FILLED, "MARKET proposal filled at supplied side quote.", price)
    if not _positive(order.limit_price):
        return FillEvaluation(ExecutionStatus.REJECTED, "A valid limit price is required.", None)
    crossed = price <= order.limit_price if order.side is OrderSide.BUY else price >= order.limit_price
    if not crossed:
        return FillEvaluation(ExecutionStatus.NOT_FILLED, "LIMIT proposal did not cross the supplied side quote.", None)
    return FillEvaluation(ExecutionStatus.FILLED, "LIMIT proposal filled at supplied side quote.", price)


def _positive(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0
