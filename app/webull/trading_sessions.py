from __future__ import annotations

from datetime import datetime

from app.broker_protocol.models import TradingSession
from app.market.calendar import MarketSession, market_session


class MarketSessionClosedError(ValueError):
    pass


def resolve_webull_trading_session(
    requested: TradingSession,
    now: datetime | None = None,
) -> str:
    if requested is TradingSession.CORE:
        return "CORE"

    if requested is TradingSession.EXTENDED:
        return "ALL"

    if requested is TradingSession.OVERNIGHT:
        return "NIGHT"

    resolved = market_session(now)

    if resolved is MarketSession.CLOSED:
        raise MarketSessionClosedError(
            "US equity markets are closed"
        )

    if resolved is MarketSession.CORE:
        return "CORE"

    if resolved is MarketSession.OVERNIGHT:
        return "NIGHT"

    return "ALL"