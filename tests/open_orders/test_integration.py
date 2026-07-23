from app.authentication import AuthenticationStateSnapshot,AuthenticationStatus
from app.open_orders import *
from app.session import DeterministicSessionManager,SessionIdentifier,SessionPolicy,SessionRequest
from tests.open_orders.fixtures import enabled_policy,request
from tests.open_orders.helpers import FakeGateway
class FakeAuthenticationService:
 def authenticate(self,request):raise AssertionError("authentication must not run")
 def logout(self):raise AssertionError("logout must not run")
 def state(self):return AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED,2)
def test_session_manager_open_orders_read_only_integration():
 manager=DeterministicSessionManager(FakeAuthenticationService(),SessionPolicy());manager.create(SessionRequest(SessionIdentifier("session-1"),"open orders"));manager.activate();gateway=FakeGateway();result=DeterministicOpenOrdersRuntime(manager,gateway,enabled_policy()).get_open_orders(request());assert result.success and len(gateway.requests)==1
