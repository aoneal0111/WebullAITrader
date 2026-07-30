"""Translate broker-neutral market events onto existing runtime projections."""

from __future__ import annotations

from decimal import Decimal

from app.market_data.models import (
    HeartbeatPayload,
    MarketEvent,
    MarketEventType,
    MarketStatusPayload,
    QuotePayload,
    ResumePayload,
    TradePayload,
    TradingHaltPayload,
)
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeHealthUpdate,
    RuntimeWatchlistQuote,
    RuntimeWatchlistUpdate,
)


def translate_market_event(
    event: MarketEvent,
    *,
    sequence: int,
    source: str,
    cycle: int,
) -> PaperRuntimeEvent | None:
    """Map an existing MarketEvent into the minimum runtime event shape."""

    if not isinstance(event, MarketEvent):
        raise TypeError("event must be a MarketEvent")

    if event.event_type is MarketEventType.QUOTE:
        payload = event.payload
        if not isinstance(payload, QuotePayload) or event.symbol is None:
            raise TypeError("QUOTE event requires a symbol and QuotePayload")
        mark_price = _quote_mark(payload)
        return PaperRuntimeEvent(
            sequence=sequence,
            timestamp=event.timestamp,
            event_type="QUOTE_UPDATED",
            message=f"Updated live quote for {event.symbol}.",
            cycle=cycle,
            symbol=event.symbol,
            mark_price=mark_price,
            source=source,
            watchlist=RuntimeWatchlistUpdate(
                symbol=event.symbol,
                quote=RuntimeWatchlistQuote(
                    timestamp=event.timestamp,
                    bid=payload.bid,
                    ask=payload.ask,
                    stale=False,
                ),
            ),
        )

    if event.event_type is MarketEventType.TRADE:
        payload = event.payload
        if not isinstance(payload, TradePayload) or event.symbol is None:
            raise TypeError("TRADE event requires a symbol and TradePayload")
        return PaperRuntimeEvent(
            sequence=sequence,
            timestamp=event.timestamp,
            event_type="MARK_UPDATED",
            message=f"Updated live trade mark for {event.symbol}.",
            cycle=cycle,
            symbol=event.symbol,
            mark_price=payload.price,
            source=source,
            watchlist=RuntimeWatchlistUpdate(
                symbol=event.symbol,
                quote=RuntimeWatchlistQuote(
                    timestamp=event.timestamp,
                    latest_price=payload.price,
                    volume=_whole_volume(payload.size),
                    stale=False,
                ),
            ),
        )

    if event.event_type is MarketEventType.HEARTBEAT:
        if not isinstance(event.payload, HeartbeatPayload):
            raise TypeError("HEARTBEAT event requires HeartbeatPayload")
        return PaperRuntimeEvent(
            sequence=sequence,
            timestamp=event.timestamp,
            event_type="MARKET_DATA_HEARTBEAT",
            message="Received Webull market-data heartbeat.",
            cycle=cycle,
            source=source,
            health=RuntimeHealthUpdate(
                market_data_status="CONNECTED",
                heartbeat_at=event.timestamp,
            ),
        )

    if event.event_type is MarketEventType.MARKET_STATUS:
        if not isinstance(event.payload, MarketStatusPayload):
            raise TypeError(
                "MARKET_STATUS event requires MarketStatusPayload"
            )
        return _market_status_event(
            event,
            sequence=sequence,
            source=source,
            cycle=cycle,
            status=event.payload.status,
        )

    if event.event_type is MarketEventType.TRADING_HALT:
        if not isinstance(event.payload, TradingHaltPayload):
            raise TypeError(
                "TRADING_HALT event requires TradingHaltPayload"
            )
        return _market_status_event(
            event,
            sequence=sequence,
            source=source,
            cycle=cycle,
            status="HALTED",
        )

    if event.event_type is MarketEventType.RESUME:
        if not isinstance(event.payload, ResumePayload):
            raise TypeError("RESUME event requires ResumePayload")
        return _market_status_event(
            event,
            sequence=sequence,
            source=source,
            cycle=cycle,
            status="OPEN",
        )

    return None


def _market_status_event(
    event: MarketEvent,
    *,
    sequence: int,
    source: str,
    cycle: int,
    status: str,
) -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=event.timestamp,
        event_type="MARKET_STATUS_UPDATED",
        message=f"Market status changed to {status}.",
        cycle=cycle,
        symbol=event.symbol,
        source=source,
        watchlist=RuntimeWatchlistUpdate(
            symbol=event.symbol,
            market_status=status,
        ),
    )


def _quote_mark(payload: QuotePayload) -> Decimal | None:
    mark = (payload.bid + payload.ask) / Decimal("2")
    return mark if mark > 0 else None


def _whole_volume(value: Decimal) -> int | None:
    integral = value.to_integral_value()
    return int(integral) if value == integral else None


__all__ = ["translate_market_event"]
