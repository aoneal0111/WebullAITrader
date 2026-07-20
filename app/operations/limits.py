from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from app.broker_protocol.models import BrokerOrderRequest,BrokerOrderType
@dataclass(frozen=True,slots=True)
class OperationalState:
 reference_price:Decimal|None;daily_submitted_notional:Decimal;open_positions:int;open_orders:int;orders_last_minute:int;market_data_timestamp:datetime;reconciliation_timestamp:datetime;unresolved_mutations:int;regular_market_open:bool
def validate_operational_limits(order:BrokerOrderRequest,state:OperationalState,config,now):
 if now.tzinfo is None:raise ValueError("current timestamp must be aware")
 if order.symbol not in config.allowed_symbols or order.symbol in config.blocked_symbols:raise ValueError("symbol is not operationally allowed")
 if order.order_type is BrokerOrderType.MARKET:raise ValueError("controlled live profile disables market orders")
 price=order.limit_price or order.stop_price or state.reference_price
 if price is None:raise ValueError("verified notional price is required")
 notional=price*order.quantity
 checks=((notional<=config.max_order_notional,"maximum order notional exceeded"),(state.daily_submitted_notional+notional<=config.max_daily_notional,"maximum daily notional exceeded"),(state.open_positions<=config.max_open_positions,"maximum open positions exceeded"),(state.open_orders<config.max_open_orders,"maximum open orders exceeded"),(state.orders_last_minute<config.max_order_rate,"maximum order frequency exceeded"),(order.quantity<=config.max_quantity_per_symbol,"maximum symbol quantity exceeded"),(state.unresolved_mutations<=config.maximum_unresolved_mutations,"unresolved mutation limit exceeded"),(state.regular_market_open,"regular market is closed"),((now-state.market_data_timestamp).total_seconds()<=config.maximum_market_data_age_seconds,"market data is stale"),((now-state.reconciliation_timestamp).total_seconds()<=config.maximum_reconciliation_age_seconds,"reconciliation is stale"))
 for passed,message in checks:
  if not passed:raise ValueError(message)
 return notional
