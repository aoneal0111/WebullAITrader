from app.authentication import AuthenticationStateSnapshot,AuthenticationStatus
from app.positions import *
from app.session import DeterministicSessionManager,SessionIdentifier,SessionPolicy,SessionRequest
from tests.positions.fixtures import enabled_policy,request
from tests.positions.helpers import FakeGateway
class FakeAuthenticationService:
 def authenticate(self,request):raise AssertionError("authentication must not run")
 def logout(self):raise AssertionError("logout must not run")
 def state(self):return AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED,2)
def test_session_manager_positions_integration_without_authentication():
 manager=DeterministicSessionManager(FakeAuthenticationService(),SessionPolicy());manager.create(SessionRequest(SessionIdentifier("session-1"),"positions access"));manager.activate();gateway=FakeGateway();result=DeterministicPositionsRuntime(manager,gateway,enabled_policy()).get_positions(request());assert result.success and len(gateway.requests)==1
