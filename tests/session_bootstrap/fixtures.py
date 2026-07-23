from app.authentication_runtime import AuthenticationRuntimeContext
from app.credentials import CredentialRequest
from app.session import SessionIdentifier,SessionRequest
from app.session_bootstrap import SessionBootstrapRequest
from tests.webull_authentication_approval.test_service import approve
def approved_profile():return approve()
def request():return SessionBootstrapRequest("bootstrap-1","attempt-1",CredentialRequest("broker","sign-in",("username_ref","password_ref","device_ref")),AuthenticationRuntimeContext("correlation-1"),SessionRequest(SessionIdentifier("session-1"),"trading"),{"synthetic":True})
