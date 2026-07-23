from app.authentication import AuthenticationStateSnapshot,AuthenticationStatus
from app.order_status import *
from app.session import DeterministicSessionManager,SessionIdentifier,SessionPolicy,SessionRequest
from tests.order_status.fixtures import enabled_policy,request
from tests.order_status.helpers import FakeGateway
class FakeAuthenticationService:
 def authenticate(self,request):raise AssertionError("authentication must not run")
 def logout(self):raise AssertionError("logout must not run")
 def state(self):return AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED,2)
def test_session_manager_status_integration_read_only():
 manager=DeterministicSessionManager(FakeAuthenticationService(),SessionPolicy());manager.create(SessionRequest(SessionIdentifier("session-1"),"order status"));manager.activate();gateway=FakeGateway();result=DeterministicOrderStatusRuntime(manager,gateway,enabled_policy()).get_order_status(request());assert result.success and len(gateway.requests)==1
