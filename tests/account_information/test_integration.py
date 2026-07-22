from app.account_information import *
from app.authentication import AuthenticationStateSnapshot, AuthenticationStatus
from app.session import DeterministicSessionManager, SessionIdentifier, SessionPolicy, SessionRequest
from tests.account_information.fixtures import enabled_policy, request
from tests.account_information.helpers import FakeGateway


class FakeAuthenticationService:
    def authenticate(self, request): raise AssertionError("authentication must not run")
    def logout(self): raise AssertionError("logout must not run")
    def state(self): return AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED,2)


def test_session_manager_to_account_runtime_integration():
    manager=DeterministicSessionManager(FakeAuthenticationService(),SessionPolicy())
    manager.create(SessionRequest(SessionIdentifier("session-1"),"account access")); manager.activate()
    gateway=FakeGateway(); result=DeterministicAccountInformationRuntime(manager,gateway,enabled_policy()).get_account_information(request())
    assert result.success and result.equity.__class__.__name__=="Decimal" and len(gateway.requests)==1
