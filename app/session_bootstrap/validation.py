from app.session_bootstrap.exceptions import SessionBootstrapDependencyError,SessionBootstrapValidationError
from app.session_bootstrap.models import SessionBootstrapRequest
from app.session_bootstrap.policies import SessionBootstrapPolicy
from app.webull_authentication_approval import WebullAuthenticationProfileApprovalResult
def validate_dependencies(approved_profile,credential_provider,authentication_runtime,session_manager,policy):
 if not isinstance(approved_profile,WebullAuthenticationProfileApprovalResult):raise SessionBootstrapDependencyError("approved profile must be WebullAuthenticationProfileApprovalResult")
 for value,operation,name in ((credential_provider,"provide","credential provider"),(authentication_runtime,"authenticate","authentication runtime"),(session_manager,"create","session manager")):
  if not callable(getattr(value,operation,None)):raise SessionBootstrapDependencyError(f"{name} has an incompatible interface")
 if not isinstance(policy,SessionBootstrapPolicy):raise SessionBootstrapDependencyError("policy must be SessionBootstrapPolicy")
 return True
def validate_request(r):
 if not isinstance(r,SessionBootstrapRequest):raise SessionBootstrapValidationError("request must be SessionBootstrapRequest")
 return r
