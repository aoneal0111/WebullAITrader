from app.authentication import AuthenticationRequest
from app.authentication_runtime.exceptions import *
from app.authentication_runtime.models import AuthenticationRuntimeResult
from app.authentication_runtime.validation import validate_dependencies,validate_request
from app.authentication_transport import AuthenticationTransportContext,AuthenticationTransportRequest,AuthenticationTransportResult
from app.credentials import CredentialResponse
class DeterministicAuthenticationRuntime:
 def __init__(self,credential_provider,connector,policy):validate_dependencies(credential_provider,connector,policy);self._provider=credential_provider;self._connector=connector;self._policy=policy
 def authenticate(self,request):
  r=validate_request(request)
  if not self._policy.enabled:raise AuthenticationRuntimeDisabledError("authentication runtime is disabled")
  try:credentials=self._provider.provide(r.credential_request)
  except Exception as exc:raise AuthenticationRuntimeCredentialError("credential lookup failed") from exc
  if not isinstance(credentials,CredentialResponse):raise AuthenticationRuntimeCredentialError("credential provider returned invalid response")
  if credentials.broker_identifier!=r.credential_request.broker_identifier or credentials.credential_purpose!=r.credential_request.credential_purpose:raise AuthenticationRuntimeCredentialError("credential response does not match request")
  names=set(r.credential_request.required_value_names)
  if not names.issubset(credentials.values):raise AuthenticationRuntimeCredentialError("credential response is missing required values")
  metadata={"attempt_id":r.attempt_id,"correlation_id":r.context.correlation_id}
  auth_request=AuthenticationRequest(r.credential_request.broker_identifier,r.credential_request.credential_purpose,r.credential_request.required_value_names,metadata)
  connector_request=AuthenticationTransportRequest(r.attempt_id,auth_request,AuthenticationTransportContext(r.context.correlation_id,r.context.metadata),r.metadata)
  try:result=self._connector.authenticate(connector_request)
  except Exception as exc:raise AuthenticationRuntimeExecutionError("authentication connector failed") from exc
  if not isinstance(result,AuthenticationTransportResult):raise AuthenticationRuntimeExecutionError("connector returned invalid result")
  return AuthenticationRuntimeResult(r.attempt_id,result.success,result,r.context,self._policy.version,{"deterministic":True})
