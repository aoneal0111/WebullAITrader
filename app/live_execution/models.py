from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from app.authorization.models import ExecutionIntent,LiveExecutionAuthorization,ValidatedExecutionIntent
from app.broker_protocol.models import (
    BrokerAccount,BrokerCash,BrokerFill,BrokerOrder,BrokerOrderRequest,BrokerOrderStatus,BrokerOrderType,
    BrokerPosition,BrokerSide,TimeInForce,
)

# Temporary compatibility aliases. Ownership is app.broker_protocol/app.authorization.
LiveSide=BrokerSide;LiveOrderType=BrokerOrderType;LiveOrderStatus=BrokerOrderStatus

@dataclass(frozen=True,slots=True)
class LocalOrder:
    request:BrokerOrderRequest;broker_order_id:str|None;status:BrokerOrderStatus;filled_quantity:Decimal
    fills:tuple[BrokerFill,...];updated_timestamp:datetime;intent:ExecutionIntent|None=None
@dataclass(frozen=True,slots=True)
class LocalPortfolioState:
    orders:tuple[LocalOrder,...]=();positions:tuple[BrokerPosition,...]=();cash:BrokerCash|None=None
@dataclass(frozen=True,slots=True)
class ReplacementRequest:
    client_order_id:str;quantity:Decimal;limit_price:Decimal|None;stop_price:Decimal|None
    validated_intent:ValidatedExecutionIntent
@dataclass(frozen=True,slots=True)
class SynchronizationDifference:
    category:str;key:str;field:str;local_value:str|None;broker_value:str|None
@dataclass(frozen=True,slots=True)
class SynchronizationReport:
    differences:tuple[SynchronizationDifference,...];broker_orders:tuple[BrokerOrder,...]
    broker_positions:tuple[BrokerPosition,...];broker_cash:BrokerCash;reconciled_state:LocalPortfolioState
