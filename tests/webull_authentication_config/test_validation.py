import pytest
from app.webull_authentication_config import *
from tests.webull_authentication_config.fixtures import configuration
def test_model_and_mapping_supported():
 model=normalize_configuration(configuration());assert validate_configuration(model) is model;assert DeterministicWebullAuthenticationProfileLoader().load(model)==DeterministicWebullAuthenticationProfileLoader().load(configuration())
def test_unknown_and_missing_fields_normalized():
 with pytest.raises(WebullAuthenticationConfigurationValidationError):DeterministicWebullAuthenticationProfileLoader().load(configuration(unknown="x"))
 raw=configuration();del raw["profile_id"]
 with pytest.raises(WebullAuthenticationConfigurationValidationError) as captured:DeterministicWebullAuthenticationProfileLoader().load(raw)
 assert captured.value.__cause__ is not None
def test_non_strict_loader_deterministically_ignores_unknown_only():
 loader=DeterministicWebullAuthenticationProfileLoader(WebullAuthenticationConfigurationLoaderPolicy(strict_unknown_fields=False));assert loader.load(configuration(unknown="x"))==loader.load(configuration())
