from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.broker_protocol.models import BrokerPosition
from app.composition.operations_position_mapper import map_broker_positions


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_map_broker_positions_preserves_and_derives_facts() -> None:
    result = map_broker_positions(
        (
            BrokerPosition(
                symbol="msft",
                quantity=Decimal("5"),
                average_price=Decimal("400"),
                market_value=Decimal("2075"),
            ),
            BrokerPosition(
                symbol="AAPL",
                quantity=Decimal("10"),
                average_price=Decimal("185.25"),
                market_value=Decimal("1900"),
            ),
        ),
        account_id="acct-redacted",
        currency="usd",
        updated_at=NOW,
    )

    assert tuple(position.symbol for position in result) == ("AAPL", "MSFT")
    assert result[0].quantity == "10"
    assert result[0].average_cost == "185.25"
    assert result[0].market_value == "1900"
    assert result[0].unrealized_gain_loss == "47.50"
    assert result[0].currency == "USD"
    assert result[0].updated_at == NOW
    assert result[1].unrealized_gain_loss == "75"


def test_map_broker_positions_uses_neutral_fallback_without_market_value() -> None:
    result = map_broker_positions(
        (
            BrokerPosition(
                symbol="AAPL",
                quantity=Decimal("2"),
                average_price=Decimal("100"),
                market_value=None,
            ),
        ),
        account_id="acct-redacted",
        currency="USD",
        updated_at=NOW,
    )

    assert result[0].market_value == "200"
    assert result[0].unrealized_gain_loss == "0"


def test_map_broker_positions_requires_immutable_tuple() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        map_broker_positions(  # type: ignore[arg-type]
            [],
            account_id="acct-redacted",
            currency="USD",
            updated_at=NOW,
        )


def test_map_broker_positions_requires_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        map_broker_positions(
            (),
            account_id="acct-redacted",
            currency="USD",
            updated_at=datetime(2026, 7, 27, 12, 0),
        )
