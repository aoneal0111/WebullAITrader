from app.authentication_runtime import AuthenticationRuntimeContext,AuthenticationRuntimeRequest
from app.credentials import CredentialRequest
def runtime_request():return AuthenticationRuntimeRequest("attempt-1",CredentialRequest("broker","sign-in",("username_ref","password_ref","device_ref")),AuthenticationRuntimeContext("correlation-1"))
