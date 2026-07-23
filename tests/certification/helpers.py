from app.authentication import AuthenticationStateSnapshot,AuthenticationStatus
from app.credentials import CredentialResponse
class CredentialProvider:
 def __init__(self,response=None,error=None):self.response=response if response is not None else CredentialResponse("broker","sign-in",{"identity":"opaque"});self.error=error;self.calls=[]
 def provide(self,request):
  self.calls.append(request)
  if self.error:raise self.error
  return self.response
class Verifier:
 def __init__(self,result=True,error=None):self.result=result;self.error=error;self.calls=[]
 def verify(self,request,credentials):
  self.calls.append((request,credentials))
  if self.error:raise self.error
  return self.result
class AuthenticationStateProvider:
 def __init__(self):self.calls=0
 def authenticate(self,request):raise AssertionError("session certification must not authenticate")
 def logout(self):raise AssertionError("session certification must not logout")
 def state(self):self.calls+=1;return AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED,1)
