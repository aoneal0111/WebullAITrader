from app.authentication import AuthenticationResult,AuthenticationStateSnapshot,AuthenticationStatus
from app.authentication_runtime import AuthenticationRuntimeContext,AuthenticationRuntimeRequest
from app.authentication_transport import AuthenticationTransportResult,AuthenticationVerificationResult
from app.credentials import CredentialRequest,CredentialResponse
def request():return AuthenticationRuntimeRequest("attempt-1",CredentialRequest("broker","sign-in",("username_ref","password_ref","device_ref")),AuthenticationRuntimeContext("correlation-1"))
def credentials():return CredentialResponse("broker","sign-in",{"username_ref":"opaque-user","password_ref":"opaque-secret","device_ref":"opaque-device"})
def transport_result(success=True):
 verification=AuthenticationVerificationResult(success,"VERIFIED" if success else "DENIED")
 auth=AuthenticationResult(True,AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED,2),"AUTHENTICATED","authentication_policy_v1") if success else None
 from app.authentication_transport import AuthenticationTransportContext
 return AuthenticationTransportResult("attempt-1",success,verification,auth,"response-1",AuthenticationTransportContext("correlation-1"),"authentication_transport_policy_v1")
class FakeProvider:
 def __init__(self,result=None,error=None):self.result=result if result is not None else credentials();self.error=error;self.calls=[]
 def provide(self,r):
  self.calls.append(r)
  if self.error:raise self.error
  return self.result
class FakeConnector:
 def __init__(self,result=None,error=None):self.result=result if result is not None else transport_result();self.error=error;self.calls=[]
 def authenticate(self,r):
  self.calls.append(r)
  if self.error:raise self.error
  return self.result
