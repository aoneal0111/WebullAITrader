from app.authentication_runtime.models import AuthenticationRuntimeRequest,AuthenticationRuntimeResult
from app.credentials.models import CredentialResponse
from app.session.models import SessionSnapshot
from app.session_bootstrap.exceptions import SessionBootstrapCredentialError,SessionBootstrapDependencyError
from app.session_bootstrap.models import *
from app.session_bootstrap.validation import validate_dependencies,validate_request
class DeterministicSessionBootstrapRuntime:
 def __init__(self,approved_profile,credential_provider,authentication_runtime,session_manager,policy):validate_dependencies(approved_profile,credential_provider,authentication_runtime,session_manager,policy);self._approval=approved_profile;self._provider=credential_provider;self._authentication_runtime=authentication_runtime;self._session_manager=session_manager;self._policy=policy
 def bootstrap(self,request):
  r=validate_request(request);profile_id=self._approval.profile_id;auth_id=r.authentication_attempt_id;session_id=r.session_request.identifier.value
  if not self._policy.enabled:return self._result(r,profile_id,auth_id,session_id,SessionBootstrapDecision.DISABLED,None,(False,False,False,False))
  if not self._approval.approved or self._approval.approved_profile is None:return self._result(r,profile_id,auth_id,session_id,SessionBootstrapDecision.APPROVAL_REJECTED,None,(True,False,False,False))
  try:credentials=self._provider.provide(r.credential_request)
  except Exception as exc:raise SessionBootstrapCredentialError("credential lookup failed") from exc
  if not isinstance(credentials,CredentialResponse):raise SessionBootstrapDependencyError("credential provider returned invalid response")
  if credentials.broker_identifier!=r.credential_request.broker_identifier or credentials.credential_purpose!=r.credential_request.credential_purpose or not set(r.credential_request.required_value_names).issubset(credentials.values):raise SessionBootstrapDependencyError("credential response does not match request")
  auth_request=AuthenticationRuntimeRequest(r.authentication_attempt_id,r.credential_request,r.authentication_context,r.metadata)
  try:authentication=self._authentication_runtime.authenticate(auth_request)
  except Exception:
   return self._result(r,profile_id,auth_id,session_id,SessionBootstrapDecision.AUTHENTICATION_FAILED,None,(True,True,False,False))
  if not isinstance(authentication,AuthenticationRuntimeResult):raise SessionBootstrapDependencyError("authentication runtime returned invalid result")
  auth_id=authentication.transport_result.response_identifier
  if not authentication.success:return self._result(r,profile_id,auth_id,session_id,SessionBootstrapDecision.AUTHENTICATION_FAILED,None,(True,True,False,False))
  try:session=self._session_manager.create(r.session_request)
  except Exception:
   return self._result(r,profile_id,auth_id,session_id,SessionBootstrapDecision.SESSION_CREATION_FAILED,None,(True,True,True,False))
  if not isinstance(session,SessionSnapshot):raise SessionBootstrapDependencyError("session manager returned invalid snapshot")
  return self._result(r,profile_id,auth_id,session_id,SessionBootstrapDecision.SUCCESS,session,(True,True,True,True))
 def _result(self,r,profile_id,auth_id,session_id,decision,handle,criteria):
  names=("policy_enabled","profile_approved","authentication_succeeded","session_created");details=("bootstrap policy enabled","validated profile approval present","authentication runtime succeeded","session manager created session")
  return SessionBootstrapResult(r.bootstrap_id,profile_id,auth_id,session_id,decision is SessionBootstrapDecision.SUCCESS,decision,handle,tuple(SessionBootstrapCriteriaResult(n,v,d) for n,v,d in zip(names,criteria,details)),{"deterministic":True,"policy_version":self._policy.version})
