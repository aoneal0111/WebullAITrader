from dataclasses import FrozenInstanceError
import pytest
from app.webull_authentication import WebullAuthenticationProfile,WebullAuthenticationProfileError
from tests.webull_authentication.fixtures import profile
def test_profile_frozen_slotted_immutable_roundtrip_and_synthetic():
 p=profile();assert WebullAuthenticationProfile.from_dict(p.to_dict())==p;assert not hasattr(p,"__dict__");assert p.metadata["synthetic"] is True
 with pytest.raises(FrozenInstanceError):p.profile_id="x"
 with pytest.raises(TypeError):p.metadata["x"]=1
@pytest.mark.parametrize("changes",[{"endpoint_url":""},{"endpoint_url":"https://user:secret@mock.invalid/a"},{"static_headers":(("X","1"),("x","2"))},{"required_response_headers":("X","x")},{"success_field_path":()},{"verification_output_field_paths":(("access_token",("x",)),)}])
def test_invalid_profiles(changes):
 with pytest.raises(WebullAuthenticationProfileError):profile(**changes)
def test_profile_serialization_contains_names_not_sensitive_values():
 rendered=repr(profile().to_dict());assert "actual-password-value" not in rendered;assert "mock.invalid" in rendered
