from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.broker_protocol.models import TradingSession
from app.webull.trading_sessions import (
    MarketSessionClosedError,
    resolve_webull_trading_session,
)

ET = ZoneInfo("America/New_York")


def test_core_mapping():
    assert (
        resolve_webull_trading_session(
            TradingSession.AUTO,
            datetime(2026, 7, 20, 10, 0, tzinfo=ET),
        )
        == "CORE"
    )


def test_premarket_mapping():
    assert (
        resolve_webull_trading_session(
            TradingSession.AUTO,
            datetime(2026, 7, 20, 8, 0, tzinfo=ET),
        )
        == "ALL"
    )


def test_overnight_mapping():
    assert (
        resolve_webull_trading_session(
            TradingSession.AUTO,
            datetime(2026, 7, 20, 2, 0, tzinfo=ET),
        )
        == "NIGHT"
    )


def test_closed_market():
    with pytest.raises(MarketSessionClosedError):
        resolve_webull_trading_session(
            TradingSession.AUTO,
            datetime(2026, 12, 25, 10, 0, tzinfo=ET),
        )