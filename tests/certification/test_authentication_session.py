import pytest
from app.authentication import AuthenticationPolicy,AuthenticationProviderError,AuthenticationRequest,DeterministicAuthenticationService
from app.session import DeterministicSessionManager,SessionIdentifier,SessionPolicy,SessionRequest,SessionStatus
from tests.certification.helpers import AuthenticationStateProvider,CredentialProvider,Verifier
def test_authentication_constructor_injection_and_exactly_once_collaborators():
 provider,verifier=CredentialProvider(),Verifier();service=DeterministicAuthenticationService(provider,verifier,AuthenticationPolicy());result=service.authenticate(AuthenticationRequest("broker","sign-in",("identity",)));assert result.success and len(provider.calls)==len(verifier.calls)==1
def test_authentication_raw_collaborator_failure_is_normalized_and_state_reset():
 service=DeterministicAuthenticationService(CredentialProvider(error=KeyError("raw")),Verifier(),AuthenticationPolicy())
 with pytest.raises(AuthenticationProviderError) as caught:service.authenticate(AuthenticationRequest("broker","sign-in",("identity",)))
 assert isinstance(caught.value.__cause__,KeyError) and service.state().status.value=="UNAUTHENTICATED"
def test_session_constructor_injection_and_no_hidden_transport_state():
 authentication=AuthenticationStateProvider();manager=DeterministicSessionManager(authentication,SessionPolicy());manager.create(SessionRequest(SessionIdentifier("session-1"),"synthetic"));snapshot=manager.activate();assert snapshot.status is SessionStatus.ACTIVE and authentication.calls==1 and not hasattr(manager,"transport")
