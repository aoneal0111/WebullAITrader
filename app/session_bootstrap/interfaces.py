from typing import Protocol
from app.session_bootstrap.models import SessionBootstrapRequest,SessionBootstrapResult
class SessionBootstrapRuntime(Protocol):
 def bootstrap(self,request:SessionBootstrapRequest)->SessionBootstrapResult:...
