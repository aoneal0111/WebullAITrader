from app.webull_gateway import WebullGatewayPolicy
from app.webull_gateway.ports import WebullGateway
def test_protocol_has_no_production_services():
 assert not WebullGatewayPolicy().gateway_enabled
 for name in ("login","refresh_session","select_account","retry"):
  assert not hasattr(WebullGateway,name)
