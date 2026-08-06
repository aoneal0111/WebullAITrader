from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class BrokerSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class BrokerOrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"


class TradingSession(StrEnum):
    AUTO = "AUTO"
    CORE = "CORE"
    EXTENDED = "EXTENDED"
    OVERNIGHT = "OVERNIGHT"


class BrokerOrderStatus(StrEnum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class BrokerOrderRequest:
    client_order_id: str
    symbol: str
    side: BrokerSide
    order_type: BrokerOrderType
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: TimeInForce
    trading_session: TradingSession = TradingSession.AUTO

    def __post_init__(self):
        from app.broker_protocol.validation import validate_order_request

        validate_order_request(self)


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: BrokerSide
    order_type: BrokerOrderType
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: TimeInForce
    status: BrokerOrderStatus
    updated_timestamp: datetime

    def __post_init__(self):
        from app.broker_protocol.validation import validate_broker_order

        validate_broker_order(self)


@dataclass(frozen=True, slots=True)
class BrokerFill:
    fill_id: str
    broker_order_id: str
    quantity: Decimal
    price: Decimal
    timestamp: datetime

    def __post_init__(self):
        from app.broker_protocol.validation import validate_fill

        validate_fill(self)


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    symbol: str
    quantity: Decimal
    average_price: Decimal
    market_value: Decimal | None

    def __post_init__(self):
        from app.broker_protocol.validation import validate_position

        validate_position(self)


@dataclass(frozen=True, slots=True)
class BrokerCash:
    settled_cash: Decimal
    unsettled_cash: Decimal | None
    currency: str
    buying_power: Decimal | None = None
    equity: Decimal | None = None

    def __post_init__(self):
        from app.broker_protocol.validation import validate_cash

        validate_cash(self)


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    account_id_redacted: str
    account_type: str
    status: str

    def __post_init__(self):
        if (
            not self.account_id_redacted
            or not self.account_type
            or not self.status
        ):
            raise ValueError("broker account fields are required")


def broker_to_dict(value):
    return asdict(value)


# Temporary compatibility names; these are aliases, not duplicate types.
LiveSide = BrokerSide
LiveOrderType = BrokerOrderType
LiveOrderStatus = BrokerOrderStatus
