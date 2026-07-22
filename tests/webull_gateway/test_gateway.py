from app.webull_gateway import *
from tests.webull_gateway.helpers import FakeGateway,STAMP
def test_fake_gateway_is_caller_configured_and_records_inputs():
 g=FakeGateway();r=LoginRequest(STAMP,"production-live");x=g.authenticate(r);assert x.authenticated and g.received==[r]
 assert g.logout(LogoutRequest(STAMP,"production-live")).logged_out
 assert g.get_account(AccountRequest(STAMP,"production-live")).buying_power==1000
