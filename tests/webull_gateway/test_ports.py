from app.webull_gateway import WebullGateway
from tests.webull_gateway.helpers import FakeGateway
def test_fake_has_complete_protocol_surface():
 g=FakeGateway();assert all(hasattr(g,x) for x in ("authenticate","logout","get_account","submit_order","cancel_order","get_order_status"))
