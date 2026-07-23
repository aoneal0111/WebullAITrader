from app.authentication import AuthenticationStateSnapshot,AuthenticationStatus
from app.market_data import *
from app.session import DeterministicSessionManager,SessionIdentifier,SessionPolicy,SessionRequest
from tests.market_data.fixtures import enabled_policy,request
from tests.market_data.helpers import FakeGateway
class FakeAuthenticationService:
 def authenticate(self,request):raise AssertionError("authentication must not run")
 def logout(self):raise AssertionError("logout must not run")
 def state(self):return AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED,2)
def test_session_manager_market_data_integration_without_authentication():
 manager=DeterministicSessionManager(FakeAuthenticationService(),SessionPolicy());manager.create(SessionRequest(SessionIdentifier("session-1"),"market data access"));manager.activate();gateway=FakeGateway();result=DeterministicMarketDataRuntime(manager,gateway,enabled_policy()).get_market_data(request());assert result.success and len(gateway.requests)==1
