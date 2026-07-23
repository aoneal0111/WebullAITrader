from app.authentication_runtime.exceptions import AuthenticationRuntimeDependencyError,AuthenticationRuntimeValidationError
from app.authentication_runtime.models import AuthenticationRuntimeRequest
from app.authentication_runtime.policies import AuthenticationRuntimePolicy
def validate_dependencies(provider,connector,policy):
 if not callable(getattr(provider,"provide",None)):raise AuthenticationRuntimeDependencyError("credential provider must implement provide")
 if not callable(getattr(connector,"authenticate",None)):raise AuthenticationRuntimeDependencyError("connector must implement authenticate")
 if not isinstance(policy,AuthenticationRuntimePolicy):raise AuthenticationRuntimeDependencyError("policy must be AuthenticationRuntimePolicy")
 return True
def validate_request(r):
 if not isinstance(r,AuthenticationRuntimeRequest):raise AuthenticationRuntimeValidationError("request must be AuthenticationRuntimeRequest")
 return r
