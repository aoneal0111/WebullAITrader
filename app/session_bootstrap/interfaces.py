from typing import Protocol
from app.session_bootstrap.models import SessionBootstrapRequest,SessionBootstrapResult
class ApprovedAuthenticationProfile(Protocol):
 profile_id:str
 approved:bool
 approved_profile:object|None
class SessionBootstrapRuntime(Protocol):
 def bootstrap(self,request:SessionBootstrapRequest)->SessionBootstrapResult:...
