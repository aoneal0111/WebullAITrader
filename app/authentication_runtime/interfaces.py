from typing import Protocol
from app.authentication_runtime.models import AuthenticationRuntimeRequest,AuthenticationRuntimeResult
class AuthenticationRuntime(Protocol):
 def authenticate(self,request:AuthenticationRuntimeRequest)->AuthenticationRuntimeResult:...
