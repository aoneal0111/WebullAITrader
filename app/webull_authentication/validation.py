from collections.abc import Mapping
from app.authentication import AuthenticationRequest
from app.http_pipeline import HTTPResponseOperation
from app.webull_authentication.exceptions import *
from app.webull_authentication.models import WebullAuthenticationProfile
from app.webull_authentication.policies import WebullAuthenticationPolicy
def validate_dependencies(profile,policy):
 if not isinstance(profile,WebullAuthenticationProfile):raise WebullAuthenticationDependencyError("profile must be WebullAuthenticationProfile")
 if not isinstance(policy,WebullAuthenticationPolicy):raise WebullAuthenticationDependencyError("policy must be WebullAuthenticationPolicy")
 return True
def validate_request(r):
 if not isinstance(r,AuthenticationRequest):raise WebullAuthenticationRequestError("request must be AuthenticationRequest")
 return r
def validate_response(r):
 if not isinstance(r,HTTPResponseOperation):raise WebullAuthenticationResponseError("response must be HTTPResponseOperation")
 if not isinstance(r.body,Mapping):raise WebullAuthenticationResponseError("response body must be a mapping")
 return r
def field_path(body,path):
 current=body
 for key in path:
  if not isinstance(current,Mapping) or key not in current:raise WebullAuthenticationVerificationError("configured response field is missing")
  current=current[key]
 return current
