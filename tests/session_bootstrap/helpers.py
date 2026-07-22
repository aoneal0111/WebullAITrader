from app.session import Session,SessionSnapshot,SessionStatus
from tests.authentication_runtime.helpers import credentials,transport_result
from app.authentication_runtime import AuthenticationRuntimeResult
class FakeProvider:
 def __init__(self,result=None,error=None):self.result=result if result is not None else credentials();self.error=error;self.calls=[]
 def provide(self,r):
  self.calls.append(r)
  if self.error:raise self.error
  return self.result
class FakeAuthenticationRuntime:
 def __init__(self,success=True,error=None,result=None):self.success=success;self.error=error;self.result=result;self.calls=[]
 def authenticate(self,r):
  self.calls.append(r)
  if self.error:raise self.error
  if self.result is not None:return self.result
  tr=transport_result(self.success);return AuthenticationRuntimeResult(r.attempt_id,self.success,tr,r.context,"authentication_runtime_policy_v1")
class FakeSessionManager:
 def __init__(self,error=None,result=None):self.error=error;self.result=result;self.calls=[]
 def create(self,r):
  self.calls.append(r)
  if self.error:raise self.error
  return self.result or SessionSnapshot(SessionStatus.CREATED,Session(r.identifier,r.purpose,SessionStatus.CREATED),(),1)
