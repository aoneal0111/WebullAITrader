import pytest
from app.webull_authentication_config import *
from tests.webull_authentication_config.fixtures import configuration
def test_public_loader_never_leaks_raw_builtin_errors_or_payload():
 raw=configuration(endpoint_url="invalid-sentinel-url")
 with pytest.raises(WebullAuthenticationConfigurationProfileError) as captured:DeterministicWebullAuthenticationProfileLoader().load(raw)
 assert isinstance(captured.value.__cause__,ValueError);assert "invalid-sentinel-url" not in str(captured.value)
def test_invalid_loader_policy_dependency():
 with pytest.raises(WebullAuthenticationConfigurationDependencyError):DeterministicWebullAuthenticationProfileLoader(object())
