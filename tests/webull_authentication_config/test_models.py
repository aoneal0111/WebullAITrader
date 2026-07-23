from dataclasses import FrozenInstanceError
import pytest
from app.webull_authentication_config import *
from tests.webull_authentication_config.fixtures import configuration
def test_input_model_frozen_slotted_roundtrip_and_no_mutable_references():
 raw=configuration();model=normalize_configuration(raw);raw["success_field_path"].append("changed");raw["profile_metadata"]["x"]=1
 assert model.success_field_path==("result","state");assert "x" not in model.profile_metadata;assert WebullAuthenticationProfileConfiguration.from_dict(model.to_dict())==model;assert not hasattr(model,"__dict__")
 with pytest.raises(FrozenInstanceError):model.profile_id="x"
def test_result_frozen_safe_and_roundtrip():
 result=DeterministicWebullAuthenticationProfileLoader().load(configuration());assert WebullAuthenticationProfileConfigurationResult.from_dict(result.to_dict())==result;assert not hasattr(result,"__dict__")
 with pytest.raises(TypeError):result.metadata["x"]=1
