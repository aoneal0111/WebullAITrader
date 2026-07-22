from app.authentication import AuthenticationStateSnapshot,AuthenticationStatus
from app.order_cancellation import *
from app.session import DeterministicSessionManager,SessionIdentifier,SessionPolicy,SessionRequest
from tests.order_cancellation.fixtures import enabled_policy,request
from tests.order_cancellation.helpers import FakeGateway
class FakeAuthenticationService:
 def authenticate(self,request):raise AssertionError("authentication must not run")
 def logout(self):raise AssertionError("logout must not run")
 def state(self):return AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED,2)
def test_session_manager_integration_only_cancels_once():
 manager=DeterministicSessionManager(FakeAuthenticationService(),SessionPolicy());manager.create(SessionRequest(SessionIdentifier("session-1"),"order cancellation"));manager.activate();gateway=FakeGateway();result=DeterministicOrderCancellationRuntime(manager,gateway,enabled_policy()).cancel_order(request());assert result.success and len(gateway.requests)==1
