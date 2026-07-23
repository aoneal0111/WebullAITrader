import pytest
from app.http_runtime import HTTPMethod
from app.webull_authentication import WebullAuthenticationPolicy,WebullAuthenticationProfile
from app.webull_authentication_config import *
from tests.webull_authentication_config.fixtures import configuration
def test_load_constructs_existing_profile_policy_and_identity():
 result=DeterministicWebullAuthenticationProfileLoader().load(configuration());assert result.configuration_id=="synthetic-config-v1";assert isinstance(result.profile,WebullAuthenticationProfile);assert isinstance(result.policy,WebullAuthenticationPolicy);assert result.profile.http_method is HTTPMethod.POST;assert result.policy.enabled
def test_explicit_disabled_policy_preserved():
 result=DeterministicWebullAuthenticationProfileLoader().load(configuration(enabled=False));assert not result.policy.enabled
@pytest.mark.parametrize("changes,error",[({"endpoint_url":"http://"},WebullAuthenticationConfigurationProfileError),({"endpoint_url":"https://user:sentinel@mock.invalid/auth"},WebullAuthenticationConfigurationProfileError),({"endpoint_url":"https://mock.invalid/auth?q=1"},WebullAuthenticationConfigurationProfileError),({"endpoint_url":"https://mock.invalid/auth#x"},WebullAuthenticationConfigurationProfileError),({"http_method":"TRACE"},WebullAuthenticationConfigurationValidationError),({"success_field_path":[]},WebullAuthenticationConfigurationProfileError),({"success_values":[]},WebullAuthenticationConfigurationProfileError),({"static_headers":[["X","1"],["x","2"]]},WebullAuthenticationConfigurationProfileError),({"required_response_headers":["X","x"]},WebullAuthenticationConfigurationProfileError),({"verification_output_field_paths":[["access_token",["x"]]]},WebullAuthenticationConfigurationProfileError)])
def test_invalid_configuration(changes,error):
 with pytest.raises(error):DeterministicWebullAuthenticationProfileLoader().load(configuration(**changes))
