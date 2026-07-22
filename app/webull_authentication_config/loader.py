from app.webull_authentication import WebullAuthenticationPolicy,WebullAuthenticationProfile
from app.webull_authentication_config.exceptions import *
from app.webull_authentication_config.models import WebullAuthenticationProfileConfigurationResult
from app.webull_authentication_config.policies import WebullAuthenticationConfigurationLoaderPolicy
from app.webull_authentication_config.serializers import normalize_configuration
class DeterministicWebullAuthenticationProfileLoader:
 def __init__(self,policy=None):
  self._policy=policy or WebullAuthenticationConfigurationLoaderPolicy()
  if not isinstance(self._policy,WebullAuthenticationConfigurationLoaderPolicy):raise WebullAuthenticationConfigurationDependencyError("loader policy must be WebullAuthenticationConfigurationLoaderPolicy")
 def load(self,configuration):
  try:c=normalize_configuration(configuration,self._policy.strict_unknown_fields)
  except WebullAuthenticationConfigurationError:raise
  except Exception as exc:raise WebullAuthenticationConfigurationValidationError("configuration validation failed") from exc
  try:p=WebullAuthenticationProfile(c.profile_id,c.endpoint_url,c.http_method,c.username_field,c.password_field,c.device_id_field,c.username_reference,c.password_reference,c.device_reference,c.success_field_path,c.success_values,c.failure_message_field_path,c.verification_output_field_paths,c.required_response_headers,c.static_headers,c.profile_metadata)
  except Exception as exc:raise WebullAuthenticationConfigurationProfileError("profile construction failed") from exc
  try:policy=WebullAuthenticationPolicy(enabled=c.enabled,strict_validation=c.strict_validation,include_device_identifier=c.include_device_identifier,require_success_indicator=c.require_success_indicator,reject_unexpected_success_values=c.reject_unexpected_success_values,metadata=c.policy_metadata)
  except Exception as exc:raise WebullAuthenticationConfigurationPolicyError("policy construction failed") from exc
  return WebullAuthenticationProfileConfigurationResult(c.configuration_id,p,policy,c.metadata)
