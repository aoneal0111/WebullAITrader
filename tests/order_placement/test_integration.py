from app.authentication import AuthenticationStateSnapshot,AuthenticationStatus
from app.order_placement import *
from app.session import DeterministicSessionManager,SessionIdentifier,SessionPolicy,SessionRequest
from tests.order_placement.fixtures import enabled_policy,request
from tests.order_placement.helpers import FakeGateway
class FakeAuthenticationService:
 def authenticate(self,request):raise AssertionError("authentication must not run")
 def logout(self):raise AssertionError("logout must not run")
 def state(self):return AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED,2)
def test_session_manager_order_integration_without_authentication():
 manager=DeterministicSessionManager(FakeAuthenticationService(),SessionPolicy());manager.create(SessionRequest(SessionIdentifier("session-1"),"order placement"));manager.activate();gateway=FakeGateway();result=DeterministicOrderPlacementRuntime(manager,gateway,enabled_policy()).place_order(request());assert result.success and len(gateway.requests)==1
