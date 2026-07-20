from __future__ import annotations
from app.market_data.models import CorporateActionPayload, MarketEvent, MarketEventType


def corporate_actions(events: tuple[MarketEvent, ...], symbol: str | None = None) -> tuple[MarketEvent, ...]:
    return tuple(item for item in events if item.event_type is MarketEventType.CORPORATE_ACTION
                 and isinstance(item.payload, CorporateActionPayload)
                 and (symbol is None or item.symbol == symbol))
