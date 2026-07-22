from app.webull_authentication_config import normalize_configuration,serialize_configuration,serialize_result,DeterministicWebullAuthenticationProfileLoader
from tests.webull_authentication_config.fixtures import configuration
def test_lists_paths_and_header_mapping_normalize_deterministically():
 model=normalize_configuration(configuration());assert model.success_field_path==("result","state");assert model.verification_output_field_paths==(("decision_code",("result","code")),);assert model.static_headers==(("accept","application/json"),("x-fixture","synthetic"))
def test_equivalent_header_inputs_serialize_equally():
 one=configuration();two=configuration(static_headers=[("accept","application/json"),("x-fixture","synthetic")]);assert normalize_configuration(one)==normalize_configuration(two);assert serialize_configuration(normalize_configuration(one))==serialize_configuration(normalize_configuration(two))
def test_safe_result_serialization_has_no_raw_input_or_secret_value():
 value=serialize_result(DeterministicWebullAuthenticationProfileLoader().load(configuration()));rendered=repr(value);assert "real-password-value" not in rendered;assert "configuration_id" in value
