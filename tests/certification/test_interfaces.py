from inspect import signature
import pytest
from app.account_information import BrokerAccountGateway
from app.authentication import AuthenticationService
from app.market_data import BrokerMarketDataGateway
from app.open_orders import BrokerOpenOrdersGateway
from app.order_cancellation import BrokerOrderCancellationGateway
from app.order_placement import BrokerOrderPlacementGateway
from app.order_status import BrokerOrderStatusGateway
from app.positions import BrokerPositionGateway
from app.session import SessionManager
from app.webull_gateway import WebullGateway
@pytest.mark.parametrize("protocol,method",[(AuthenticationService,"authenticate"),(SessionManager,"state"),(BrokerAccountGateway,"get_account_information"),(BrokerPositionGateway,"get_positions"),(BrokerMarketDataGateway,"get_market_data"),(BrokerOrderPlacementGateway,"place_order"),(BrokerOrderStatusGateway,"get_order_status"),(BrokerOpenOrdersGateway,"get_open_orders"),(BrokerOrderCancellationGateway,"cancel_order")])
def test_narrow_contracts_expose_expected_operation(protocol,method):assert callable(getattr(protocol,method))
@pytest.mark.parametrize("protocol,method",[(BrokerAccountGateway,"get_account_information"),(BrokerPositionGateway,"get_positions"),(BrokerMarketDataGateway,"get_market_data"),(BrokerOrderPlacementGateway,"place_order"),(BrokerOrderStatusGateway,"get_order_status"),(BrokerOpenOrdersGateway,"get_open_orders"),(BrokerOrderCancellationGateway,"cancel_order")])
def test_gateway_operation_accepts_exactly_one_request(protocol,method):assert list(signature(getattr(protocol,method)).parameters)==["self","request"]
def test_webull_gateway_is_protocol_only_and_not_a_runtime_adapter():
 assert getattr(WebullGateway,"_is_protocol",False)
 assert not hasattr(WebullGateway,"get_positions") and not hasattr(WebullGateway,"get_market_data") and not hasattr(WebullGateway,"get_open_orders")
